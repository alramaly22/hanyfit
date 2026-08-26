"""Session-backed shopping cart.

Design notes
------------
The session stores only identifiers and quantities. Prices, names and stock are
always re-read from the database when the cart is rendered, so a customer
cannot edit their cookie to change what something costs, and a price change in
the admin is reflected immediately in every open cart.
"""

import logging

from django.conf import settings

from . import geo
from .models import Product, ProductVariant, SizeChoices
from .pricing import ZERO, convert_to_country, currency_label_for, shipping_for, to_money

logger = logging.getLogger(__name__)

CART_SESSION_KEY = "cart"
# Separate session key from CART_SESSION_KEY on purpose: a coupon is applied
# to the cart, not stored as one of its lines, and clearing the cart after an
# order is placed should not silently keep an old coupon code lingering
# around for the next, unrelated cart.
COUPON_SESSION_KEY = "cart_coupon_code"


def line_key(product_id, size):
    """Identify an apparel cart line. Same product in two sizes is two lines.

    Format is "<product_id>:<size>" (e.g. "12:M"). Meal lines use a disjoint
    "meal:<meal_id>" format (see meal_line_key) so the two can never collide
    -- a product_id is always numeric, "meal" never is -- and existing
    session data written before HanyFit Meals existed keeps resolving
    exactly as before.
    """
    return f"{product_id}:{size}"


def meal_line_key(meal_id):
    """Identify a meal cart line. Meals have no size, so one line per meal."""
    return f"meal:{meal_id}"


class CartLine:
    """One resolved apparel cart line, ready for display."""

    kind = "apparel"

    __slots__ = ("key", "product", "variant", "size", "quantity", "unit_price")

    def __init__(self, key, product, variant, size, quantity, unit_price):
        self.key = key
        self.product = product
        self.variant = variant
        self.size = size
        self.quantity = quantity
        self.unit_price = unit_price

    @property
    def line_total(self):
        return to_money(self.unit_price * self.quantity)

    @property
    def max_quantity(self):
        stock = self.variant.stock if self.variant else 0
        return min(stock, settings.STORE_MAX_ITEM_QUANTITY)

    @property
    def image_url(self):
        return self.product.front_image_url


class MealCartLine:
    """One resolved HanyFit Meals cart line. Parallel interface to CartLine
    (key, quantity, unit_price, line_total, max_quantity, image_url) so
    templates and checkout can treat both kinds mostly uniformly, branching
    on ``kind`` only where they genuinely differ (meals have no size/variant).
    """

    kind = "meal"

    __slots__ = ("key", "meal", "quantity", "unit_price")

    def __init__(self, key, meal, quantity, unit_price):
        self.key = key
        self.meal = meal
        self.quantity = quantity
        self.unit_price = unit_price

    @property
    def size(self):
        return ""

    @property
    def line_total(self):
        return to_money(self.unit_price * self.quantity)

    @property
    def max_quantity(self):
        return settings.MEALS_MAX_CART_QUANTITY

    @property
    def image_url(self):
        return self.meal.image_url


class Cart:
    """Wraps the cart stored in ``request.session``."""

    def __init__(self, request):
        self.session = request.session
        raw = self.session.get(CART_SESSION_KEY)
        if not isinstance(raw, dict):
            raw = {}
        self._raw = raw
        self._lines = None  # resolved lazily, then cached per request
        # The country the visitor is currently browsing in (see store/geo.py).
        # Decides which currency line prices are converted into, and which
        # country's shipping fee/free-shipping threshold apply.
        self.country = geo.get_country(request)
        # Set when resolving had to trim or drop something, so checkout can stop
        # and tell the customer instead of quietly changing their order.
        self.adjusted = False
        self.adjustment_message = ""

    # -- persistence ----------------------------------------------------

    def save(self):
        self.session[CART_SESSION_KEY] = self._raw
        self.session.modified = True
        self._lines = None  # force a re-resolve on next access

    def clear(self):
        self._raw = {}
        self.session[CART_SESSION_KEY] = {}
        self.session.pop(COUPON_SESSION_KEY, None)
        self.session.modified = True
        self._lines = []

    # -- mutation -------------------------------------------------------

    def add(self, product, size, quantity=1, *, replace=False):
        """Add a product/size to the cart.

        Returns (ok, message). The quantity is clamped to available stock
        rather than rejected outright, so a customer asking for 5 of the 3 left
        gets 3 and a clear message instead of a dead end.
        """
        if size not in SizeChoices.values:
            return False, "Choose a valid size."

        variant = product.variants.filter(size=size, is_active=True).first()
        if not variant or variant.stock <= 0:
            return False, f"Size {size} is sold out."

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1
        if quantity < 1:
            quantity = 1

        key = line_key(product.pk, size)
        current = 0 if replace else self._raw.get(key, {}).get("quantity", 0)
        requested = quantity if replace else current + quantity

        ceiling = min(variant.stock, settings.STORE_MAX_ITEM_QUANTITY)
        final = min(requested, ceiling)

        self._raw[key] = {
            "product_id": product.pk,
            "size": size,
            "quantity": final,
        }
        self.save()

        if final < requested:
            if final == variant.stock:
                return True, f"Only {final} left in size {size}."
            return True, f"Limit of {ceiling} per item."
        return True, f"{product.name} ({size}) added to your bag."

    def add_meal(self, meal, quantity=1, *, replace=False):
        """Add a HanyFit Meals item to the cart.

        Meals have no size/variant, so this is the meal counterpart of
        ``add()`` rather than a branch inside it -- keeping the two
        deliberately separate means the apparel path above is untouched.
        Daily delivery capacity is *not* checked here: it is date-specific,
        and the delivery date is only chosen at checkout (see
        meals/services/availability.py, applied in store/views.py
        ``_place_order``). This only clamps to a sane per-line ceiling.
        """
        if not meal.is_active:
            return False, "This meal is currently unavailable."
        if getattr(settings, "MEALS_EGYPT_ONLY", True) and self.country != "EG":
            return False, "HanyFit Meals currently deliver within Egypt only."

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1
        if quantity < 1:
            quantity = 1

        key = meal_line_key(meal.pk)
        current = 0 if replace else self._raw.get(key, {}).get("quantity", 0)
        requested = quantity if replace else current + quantity

        ceiling = settings.MEALS_MAX_CART_QUANTITY
        final = min(requested, ceiling)

        self._raw[key] = {"meal_id": meal.pk, "quantity": final}
        self.save()

        if final < requested:
            return True, f"Limit of {ceiling} per meal."
        return True, f"{meal.name} added to your bag."

    def set_quantity(self, key, quantity):
        """Set an exact quantity. Zero or less removes the line."""
        entry = self._raw.get(key)
        if not entry:
            return False, "That item is no longer in your bag."

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return False, "Enter a valid quantity."

        if quantity <= 0:
            return self.remove(key)

        if "meal_id" in entry:
            from meals.models import Meal

            meal = Meal.objects.filter(pk=entry["meal_id"], is_active=True).first()
            if not meal:
                self.remove(key)
                return False, "That meal is no longer available and was removed from your bag."

            ceiling = settings.MEALS_MAX_CART_QUANTITY
            final = min(quantity, ceiling)
            entry["quantity"] = final
            self.save()

            if final < quantity:
                return True, f"Only {final} allowed per meal."
            return True, "Bag updated."

        variant = ProductVariant.objects.filter(
            product_id=entry["product_id"],
            size=entry["size"],
            is_active=True,
        ).first()
        if not variant or variant.stock <= 0:
            self.remove(key)
            return False, "That size just sold out and was removed from your bag."

        ceiling = min(variant.stock, settings.STORE_MAX_ITEM_QUANTITY)
        final = min(quantity, ceiling)
        entry["quantity"] = final
        self.save()

        if final < quantity:
            return True, f"Only {final} available."
        return True, "Bag updated."

    def remove(self, key):
        if key in self._raw:
            del self._raw[key]
            self.save()
            return True, "Item removed."
        return False, "That item is no longer in your bag."

    # -- reading --------------------------------------------------------

    @property
    def lines(self):
        if self._lines is None:
            self._lines = self._resolve()
        return self._lines

    def _resolve(self):
        """Join the session data against the database in a single query pass.

        Lines whose product/meal or size has since been deactivated are
        dropped, and quantities above remaining stock/limits are trimmed, so
        the cart can never present something that cannot actually be sold.
        """
        if not self._raw:
            return []

        product_ids = {
            entry.get("product_id")
            for entry in self._raw.values()
            if isinstance(entry, dict) and entry.get("product_id")
        }
        meal_ids = {
            entry.get("meal_id")
            for entry in self._raw.values()
            if isinstance(entry, dict) and entry.get("meal_id")
        }
        if not product_ids and not meal_ids:
            return []

        products = {
            product.pk: product
            for product in Product.objects.active()
            .with_variants()
            .filter(pk__in=product_ids)
        } if product_ids else {}

        meals = {}
        if meal_ids:
            from meals.models import Meal

            meals = {meal.pk: meal for meal in Meal.objects.active().filter(pk__in=meal_ids)}

        meals_blocked = getattr(settings, "MEALS_EGYPT_ONLY", True) and self.country != "EG"

        lines = []
        changed = False
        self.adjusted = False
        self.adjustment_message = ""

        for key, entry in list(self._raw.items()):
            if not isinstance(entry, dict):
                del self._raw[key]
                changed = True
                continue

            if "meal_id" in entry:
                meal = meals.get(entry.get("meal_id"))
                if not meal or meals_blocked:
                    del self._raw[key]
                    changed = True
                    self.adjusted = True
                    self.adjustment_message = (
                        "HanyFit Meals currently deliver within Egypt only; a meal "
                        "was removed from your bag."
                        if meals_blocked
                        else "A meal in your bag is no longer available and was removed."
                    )
                    continue

                quantity = entry.get("quantity", 1)
                try:
                    quantity = int(quantity)
                except (TypeError, ValueError):
                    quantity = 1

                ceiling = settings.MEALS_MAX_CART_QUANTITY
                clamped = max(1, min(quantity, ceiling))
                if clamped != quantity:
                    entry["quantity"] = clamped
                    changed = True
                    self.adjusted = True
                    self.adjustment_message = (
                        f"Only {clamped} allowed per meal. Your bag has been updated."
                    )

                lines.append(
                    MealCartLine(key=key, meal=meal, quantity=clamped, unit_price=meal.price)
                )
                continue

            product = products.get(entry.get("product_id"))
            size = entry.get("size")
            if not product or size not in SizeChoices.values:
                del self._raw[key]
                changed = True
                self.adjusted = True
                self.adjustment_message = (
                    "An item in your bag is no longer available and was removed."
                )
                continue

            variant = next(
                (v for v in product.variants.all() if v.size == size and v.is_active),
                None,
            )
            if not variant or variant.stock <= 0:
                del self._raw[key]
                changed = True
                self.adjusted = True
                self.adjustment_message = (
                    f"{product.name} in size {size} has sold out and was removed "
                    "from your bag."
                )
                continue

            quantity = entry.get("quantity", 1)
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                quantity = 1

            ceiling = min(variant.stock, settings.STORE_MAX_ITEM_QUANTITY)
            clamped = max(1, min(quantity, ceiling))
            if clamped != quantity:
                entry["quantity"] = clamped
                changed = True
                self.adjusted = True
                self.adjustment_message = (
                    f"Only {clamped} left of {product.name} in size {size}. "
                    "Your bag has been updated."
                )

            lines.append(
                CartLine(
                    key=key,
                    product=product,
                    variant=variant,
                    size=size,
                    quantity=clamped,
                    unit_price=convert_to_country(product.price, self.country),
                )
            )

        if changed:
            self.session[CART_SESSION_KEY] = self._raw
            self.session.modified = True

        return lines

    def __iter__(self):
        return iter(self.lines)

    def __len__(self):
        return len(self.lines)

    def __bool__(self):
        return bool(self.lines)

    @property
    def count(self):
        """Total number of garments, used for the header badge."""
        return sum(line.quantity for line in self.lines)

    @property
    def subtotal(self):
        return to_money(sum((line.line_total for line in self.lines), ZERO))

    @property
    def has_meal_items(self):
        return any(line.kind == "meal" for line in self.lines)

    @property
    def meal_subtotal(self):
        """Subtotal of meal lines only -- coupons in this phase only ever
        discount HanyFit Meals, never the clothing store (see coupon
        service / Coupon.AppliesTo)."""
        return to_money(
            sum((line.line_total for line in self.lines if line.kind == "meal"), ZERO)
        )

    @property
    def shipping(self):
        return shipping_for(self.subtotal, self.country)

    # -- coupon (HanyFit Meals only) -------------------------------------

    def set_coupon(self, code):
        self.session[COUPON_SESSION_KEY] = (code or "").strip().upper()
        self.session.modified = True

    def clear_coupon(self):
        self.session.pop(COUPON_SESSION_KEY, None)
        self.session.modified = True

    @property
    def coupon_code(self):
        return self.session.get(COUPON_SESSION_KEY, "")

    @property
    def coupon(self):
        """The currently-applied, still-valid Coupon, or None. A coupon that
        has expired/been deactivated/used up since it was applied silently
        stops discounting rather than erroring the whole cart page -- the
        authoritative re-check happens again at order placement."""
        code = self.coupon_code
        if not code or not self.has_meal_items:
            return None
        from meals.models import Coupon

        coupon = Coupon.objects.filter(code=code).first()
        if not coupon or not coupon.is_valid_now() or not coupon.applies_to_meals():
            return None
        if self.meal_subtotal < coupon.min_subtotal:
            return None
        return coupon

    @property
    def discount_amount(self):
        coupon = self.coupon
        if not coupon:
            return ZERO
        return coupon.calculate_discount(self.meal_subtotal)

    @property
    def total(self):
        return to_money(self.subtotal + self.shipping - self.discount_amount)

    @property
    def currency(self):
        return currency_label_for(self.country)

    def to_json(self):
        """Serialise the cart for the JSON responses used by the AJAX flows."""
        return {
            "count": self.count,
            "subtotal": str(self.subtotal),
            "shipping": str(self.shipping),
            "discount": str(self.discount_amount),
            "coupon_code": self.coupon.code if self.coupon else "",
            "total": str(self.total),
            "country": self.country,
            "currency": self.currency,
            "lines": [
                {
                    "key": line.key,
                    "kind": line.kind,
                    "slug": line.meal.slug if line.kind == "meal" else line.product.slug,
                    "name": line.meal.name if line.kind == "meal" else line.product.name,
                    "size": line.size,
                    "quantity": line.quantity,
                    "unit_price": str(line.unit_price),
                    "line_total": str(line.line_total),
                    "image": line.image_url,
                    "max_quantity": line.max_quantity,
                }
                for line in self.lines
            ],
        }

    def tiktok_contents(self):
        """Cart contents in the shape TikTok's ``contents`` property expects."""
        return [
            {
                "content_id": line.meal.slug if line.kind == "meal" else line.product.slug,
                "content_type": "product",
                "content_name": line.meal.name if line.kind == "meal" else line.product.name,
                "quantity": line.quantity,
                "price": float(line.unit_price),
            }
            for line in self.lines
        ]

    def fb_content_ids(self):
        """Cart contents in the shape Meta Pixel's ``content_ids``/``contents``
        properties expect -- same underlying lines as tiktok_contents above,
        just Meta's slightly different key names ("id" not "content_id")."""
        return [
            {
                "id": line.meal.slug if line.kind == "meal" else line.product.slug,
                "quantity": line.quantity,
            }
            for line in self.lines
        ]


def get_cart(request):
    """Return the cart for this request, building it at most once.

    The view and the header badge both need the cart. Without this the page
    would resolve it twice and run the product and variant queries twice for
    every request.
    """
    cached = getattr(request, "_store_cart", None)
    if cached is None:
        cached = Cart(request)
        request._store_cart = cached
    return cached
