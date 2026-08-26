"""Store views: catalogue, cart, checkout, payment return and gateway webhook."""

import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import geo
from .cart import get_cart
from .forms import CheckoutForm
from .models import Order, OrderItem, Product, ProductVariant, WebhookEvent
from meals.models import Meal
from meals.services import availability as meal_availability
from meals.services import coupons as coupons_service
from meals.services import notifications as meals_notifications
from .pricing import (
    ZERO,
    amount_until_free_shipping,
    convert_to_country,
    currency_label_for,
    free_shipping_threshold_for,
    shipping_for,
    to_money,
)
from .services import fawaterk, notifications, tiktok

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wants_json(request):
    """True for fetch() calls, false for a plain form post."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    return "application/json" in request.headers.get("Accept", "")


def _json_or_redirect(request, *, ok, message, fallback, status=200):
    """Answer AJAX with JSON, and a normal form post with a redirect."""
    if _wants_json(request):
        cart = get_cart(request)
        return JsonResponse(
            {"ok": ok, "message": message, "cart": cart.to_json()},
            status=status if ok else 400,
        )
    if message:
        (messages.success if ok else messages.error)(request, message)
    return redirect(fallback)


def _absolute(request, path):
    return request.build_absolute_uri(path)


def _on_payment_confirmed(order, request=None):
    """Side effects that run exactly once, when a payment is first verified.

    Called from both confirmation paths (the webhook and the success-page
    lookup), so whichever arrives first wins and the other becomes a no-op.
    The caller is responsible for only invoking this when ``mark_paid()``
    returned True.

    Neither call below can raise: analytics and email are best-effort and must
    never turn a successful payment into an error response.
    """
    logger.info("Payment confirmed for order %s", order.order_number)
    tiktok.send_purchase(order, request=request)
    notifications.send_order_notification(order, reason="paid")

    # A subscription order (see meals/views.py::_start_subscription_payment)
    # is an Order like any other for Fawaterk's purposes, but paying it must
    # also flip the linked Subscription to paid -- Order.mark_paid alone has
    # no reason to know Subscription exists.
    if order.subscription_id:
        order.subscription.mark_paid(
            invoice_id=order.fawaterk_invoice_id, invoice_key=order.fawaterk_invoice_key,
        )
        meals_notifications.notify(
            "subscription_paid", to_email=order.subscription.email, name=order.subscription.full_name,
        )
    elif order.has_meal_items:
        meals_notifications.notify(
            "order_paid", to_email=order.email, name=order.full_name, ref=order.order_number,
        )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

@require_GET
def store(request):
    products = list(Product.objects.active().with_variants())
    country = geo.get_country(request)

    return render(
        request,
        "store/store.html",
        {
            "products": products,
            "free_shipping_threshold": free_shipping_threshold_for(country),
            "page_title": "HANY APPAREL",
        },
    )


@require_GET
def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.active().with_variants(),
        slug=slug,
    )

    variants = product.available_variants()
    country = geo.get_country(request)
    price = convert_to_country(product.price, country)
    currency = currency_label_for(country)

    # ViewContent, reported from the server as well as the pixel. The shared
    # event id lets TikTok collapse the two into one.
    event_id = tiktok.new_event_id()
    tiktok.send_event(
        tiktok.VIEW_CONTENT,
        event_id=event_id,
        request=request,
        properties={
            "currency": currency,
            "value": float(price),
            "content_type": "product",
            "contents": [
                {
                    "content_id": product.slug,
                    "content_type": "product",
                    "content_name": product.name,
                    "quantity": 1,
                    "price": float(price),
                }
            ],
        },
    )

    related = (
        Product.objects.active()
        .with_variants()
        .exclude(pk=product.pk)[:3]
    )

    return render(
        request,
        "store/product_detail.html",
        {
            "product": product,
            "display_price": price,
            "variants": variants,
            "related_products": related,
            "view_content_event_id": event_id,
            "max_quantity": settings.STORE_MAX_ITEM_QUANTITY,
            "page_title": product.name,
        },
    )


@require_POST
def set_country(request):
    """Switch the delivery country for this session (header switcher).

    Only ever changes the session; it never touches an existing order, so a
    customer cannot alter the price of something they already bought by
    flipping this after the fact.
    """
    code = (request.POST.get("country") or "").strip().upper()
    ok = geo.set_country(request, code)
    next_url = request.POST.get("next") or reverse("store")
    # Only ever redirect back into this site, never to an attacker-supplied
    # off-site URL.
    if not next_url.startswith("/"):
        next_url = reverse("store")

    if not ok:
        messages.error(request, "That delivery country is not available.")
    return redirect(next_url)


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

@never_cache
@require_GET
def cart_view(request):
    cart = get_cart(request)
    subtotal = cart.subtotal

    return render(
        request,
        "store/cart.html",
        {
            "cart": cart,
            "subtotal": subtotal,
            "shipping": cart.shipping,
            "total": cart.total,
            "until_free_shipping": amount_until_free_shipping(subtotal, cart.country),
            "free_shipping_threshold": free_shipping_threshold_for(cart.country),
            "page_title": "Your bag",
        },
    )


@require_POST
def cart_add(request):
    slug = (request.POST.get("slug") or "").strip()
    size = (request.POST.get("size") or "").strip().upper()
    quantity = request.POST.get("quantity", 1)

    product = Product.objects.active().with_variants().filter(slug=slug).first()
    if not product:
        return _json_or_redirect(
            request,
            ok=False,
            message="That product is no longer available.",
            fallback="store",
        )

    if not size:
        return _json_or_redirect(
            request,
            ok=False,
            message="Choose a size first.",
            fallback=product.get_absolute_url(),
        )

    cart = get_cart(request)
    ok, message = cart.add(product, size, quantity)

    event_id = ""
    if ok:
        line_price = convert_to_country(product.price, cart.country)
        # One id for both reports of this add-to-cart, so TikTok deduplicates.
        event_id = tiktok.new_event_id()
        tiktok.send_event(
            tiktok.ADD_TO_CART,
            event_id=event_id,
            request=request,
            properties={
                "currency": cart.currency,
                "value": float(line_price),
                "content_type": "product",
                "contents": [
                    {
                        "content_id": product.slug,
                        "content_type": "product",
                        "content_name": product.name,
                        "quantity": int(quantity or 1),
                        "price": float(line_price),
                    }
                ],
            },
        )

    if _wants_json(request):
        return JsonResponse(
            {
                "ok": ok,
                "message": message,
                "event_id": event_id,
                "cart": cart.to_json(),
            },
            status=200 if ok else 400,
        )

    return _json_or_redirect(
        request,
        ok=ok,
        message=message,
        fallback=product.get_absolute_url(),
    )


@require_POST
def cart_add_meal(request):
    """HanyFit Meals counterpart of cart_add. Kept as a separate view (not a
    branch inside cart_add) because a meal has no size/variant to validate
    and no TikTok product-catalogue wiring beyond what Cart.to_json already
    reports -- keeping it separate means the apparel path above is
    untouched."""
    slug = (request.POST.get("slug") or "").strip()
    quantity = request.POST.get("quantity", 1)

    meal = Meal.objects.active().filter(slug=slug).first()
    if not meal:
        return _json_or_redirect(
            request, ok=False, message="That meal is no longer available.", fallback="meals:home",
        )

    cart = get_cart(request)
    ok, message = cart.add_meal(meal, quantity)

    if _wants_json(request):
        return JsonResponse(
            {"ok": ok, "message": message, "cart": cart.to_json()},
            status=200 if ok else 400,
        )
    return _json_or_redirect(request, ok=ok, message=message, fallback=meal.get_absolute_url())


@require_POST
def cart_apply_coupon(request):
    """Preview a coupon's discount against the current bag. The discount is
    recomputed (and the coupon actually redeemed) again, from scratch,
    server-side, when the order is placed -- this endpoint never itself
    marks the coupon as used."""
    code = (request.POST.get("code") or "").strip()
    cart = get_cart(request)

    if not cart.has_meal_items:
        return _json_or_redirect(
            request, ok=False, message="Coupons only apply to HanyFit Meals items.", fallback="cart",
        )

    try:
        coupons_service.validate(
            code, subtotal=cart.meal_subtotal, phone="", kind="meals",
        )
    except coupons_service.CouponError as exc:
        cart.clear_coupon()
        if _wants_json(request):
            return JsonResponse({"ok": False, "message": str(exc), "cart": cart.to_json()}, status=400)
        messages.error(request, str(exc))
        return redirect("cart")

    cart.set_coupon(code)
    message = "Coupon applied."
    if _wants_json(request):
        return JsonResponse({"ok": True, "message": message, "cart": cart.to_json()})
    messages.success(request, message)
    return redirect("cart")


@require_POST
def cart_remove_coupon(request):
    cart = get_cart(request)
    cart.clear_coupon()
    if _wants_json(request):
        return JsonResponse({"ok": True, "message": "Coupon removed.", "cart": cart.to_json()})
    return redirect("cart")


@require_POST
def cart_update(request):
    key = (request.POST.get("key") or "").strip()
    quantity = request.POST.get("quantity", 0)

    cart = get_cart(request)
    ok, message = cart.set_quantity(key, quantity)
    return _json_or_redirect(request, ok=ok, message=message, fallback="cart")


@require_POST
def cart_remove(request):
    key = (request.POST.get("key") or "").strip()
    cart = get_cart(request)
    ok, message = cart.remove(key)
    return _json_or_redirect(request, ok=ok, message=message, fallback="cart")


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

class _OutOfStock(Exception):
    """Internal signal that a cart line cannot be fulfilled."""


@never_cache
def checkout(request):
    cart = get_cart(request)

    if not cart:
        messages.info(request, "Your bag is empty.")
        return redirect("store")

    checkout_event_id = ""

    if request.method == "POST":
        form = CheckoutForm(request.POST, country=cart.country, requires_delivery=cart.has_meal_items)
        if form.is_valid():
            return _place_order(request, cart, form)
        messages.error(request, "Please check the highlighted fields.")
    else:
        # Carry over a coupon already applied on the cart page (session-stored,
        # see Cart.set_coupon) so it is not silently dropped from the order --
        # _place_order only ever reads coupon_code from this form, never the
        # cart's own session state, to keep "what the order actually charged"
        # traceable to one place (the submitted form).
        form = CheckoutForm(
            country=cart.country,
            requires_delivery=cart.has_meal_items,
            initial={"coupon_code": cart.coupon_code},
        )
        # InitiateCheckout fires once, when the page is first opened, not on a
        # failed re-submit, so the funnel numbers stay honest.
        checkout_event_id = tiktok.new_event_id()
        tiktok.send_event(
            tiktok.INITIATE_CHECKOUT,
            event_id=checkout_event_id,
            request=request,
            properties={
                "currency": cart.currency,
                "value": float(cart.total),
                "content_type": "product",
                "contents": cart.tiktok_contents(),
            },
        )

    return render(
        request,
        "store/checkout.html",
        {
            "form": form,
            "cart": cart,
            "subtotal": cart.subtotal,
            "shipping": cart.shipping,
            "discount": cart.discount_amount,
            "coupon": cart.coupon,
            "total": cart.total,
            "requires_delivery": cart.has_meal_items,
            "online_payment_available": fawaterk.is_configured(),
            "checkout_event_id": checkout_event_id,
            "tiktok_contents": cart.tiktok_contents(),
            "fb_content_ids": cart.fb_content_ids(),
            "page_title": "Checkout",
        },
    )


def _place_order(request, cart, form):
    """Create the order, reserve stock/meal capacity, redeem any coupon, and
    hand off to payment.

    Everything runs inside one transaction: a row lock on each apparel
    variant (unchanged from before HanyFit Meals existed) plus a row lock on
    each meal's MealDailyAvailability for the chosen delivery date, plus a
    row lock on the coupon if one is applied. Either the whole order commits
    or none of it does -- an apparel item selling out cannot leave a meal
    half-reserved and vice versa.
    """
    lines = cart.lines
    if not lines:
        messages.info(request, "Your bag is empty.")
        return redirect("store")

    # Resolving the cart may have trimmed a quantity or dropped a sold-out line.
    # The customer reviewed different numbers, so send them back to look rather
    # than charging them for an order they did not confirm.
    if cart.adjusted:
        messages.error(
            request,
            cart.adjustment_message
            or "Your bag changed while you were checking out. Please review it.",
        )
        return redirect("cart")

    apparel_lines = [line for line in lines if line.kind == "apparel"]
    meal_lines = [line for line in lines if line.kind == "meal"]
    delivery_date = form.cleaned_data.get("delivery_date")

    if meal_lines and not delivery_date:
        # Belt and braces -- CheckoutForm already makes this required whenever
        # requires_delivery is True, but a cart could only pick up a meal line
        # between the form being built and the transaction starting.
        messages.error(request, "Choose a delivery date for your meals.")
        return redirect("checkout")

    try:
        with transaction.atomic():
            locked = {
                variant.pk: variant
                for variant in ProductVariant.objects.select_for_update().filter(
                    pk__in=[line.variant.pk for line in apparel_lines]
                )
            }
            for line in apparel_lines:
                variant = locked.get(line.variant.pk)
                if not variant or not variant.is_active:
                    raise _OutOfStock(f"{line.product.name} ({line.size}) is no longer available.")
                if variant.stock < line.quantity:
                    raise _OutOfStock(
                        f"Only {variant.stock} left of {line.product.name} in size {line.size}."
                    )

            for line in meal_lines:
                try:
                    meal_availability.reserve(line.meal.pk, delivery_date, line.quantity)
                except meal_availability.MealUnavailable as exc:
                    raise _OutOfStock(str(exc)) from exc

            meal_subtotal = to_money(sum((line.line_total for line in meal_lines), ZERO))
            subtotal = to_money(sum(line.line_total for line in lines))
            shipping = shipping_for(subtotal, cart.country)

            coupon = None
            discount_amount = ZERO
            coupon_code = form.cleaned_data.get("coupon_code")
            if coupon_code and meal_lines:
                try:
                    coupon = coupons_service.validate(
                        coupon_code, subtotal=meal_subtotal, phone=form.cleaned_data["phone"], kind="meals",
                    )
                except coupons_service.CouponError as exc:
                    raise _OutOfStock(str(exc)) from exc
                discount_amount = coupon.calculate_discount(meal_subtotal)
            elif coupon_code and not meal_lines:
                raise _OutOfStock("Coupons only apply to HanyFit Meals items.")

            total = to_money(subtotal + shipping - discount_amount)

            order = form.save(commit=False)
            order.payment_method = form.cleaned_data["payment"]
            order.country = cart.country
            order.subtotal = subtotal
            order.shipping_cost = shipping
            order.discount_amount = discount_amount
            order.coupon = coupon
            order.total_price = total
            # The cart already converted every line into the visitor's
            # country currency (store/cart.py), so the order is created,
            # invoiced and charged in that same currency end to end.
            order.currency = cart.currency
            order.session_key = request.session.session_key or ""
            order.tiktok_event_id = tiktok.new_event_id()
            if not meal_lines:
                # Never persist a stray delivery date/slot on an apparel-only
                # order even if one somehow made it into cleaned_data.
                order.delivery_date = None
                order.delivery_slot = None
            order.save()

            if coupon is not None:
                try:
                    coupons_service.redeem(
                        coupon.pk,
                        subtotal=meal_subtotal,
                        phone=form.cleaned_data["phone"],
                        order=order,
                    )
                except coupons_service.CouponError as exc:
                    # Re-validated at the moment of redemption (locked row) and
                    # lost the race -- e.g. usage_limit hit by a concurrent
                    # checkout between validate() and here. Whole order rolls
                    # back so the customer is never charged the discounted
                    # price for a coupon that didn't actually apply.
                    raise _OutOfStock(str(exc)) from exc

            OrderItem.objects.bulk_create(
                [
                    OrderItem(
                        order=order,
                        product=line.product,
                        variant=line.variant,
                        item_type=OrderItem.ItemType.APPAREL,
                        product_name=line.product.name,
                        product_image=line.product.front_image,
                        size=line.size,
                        quantity=line.quantity,
                        unit_price=line.unit_price,
                    )
                    for line in apparel_lines
                ]
                + [
                    OrderItem(
                        order=order,
                        meal=line.meal,
                        item_type=OrderItem.ItemType.MEAL,
                        product_name=line.meal.name,
                        product_image=line.meal.image,
                        size="",
                        quantity=line.quantity,
                        unit_price=line.unit_price,
                    )
                    for line in meal_lines
                ]
            )

            # Reserve apparel stock now. Released again if an online payment
            # fails (Order.release_stock, which also releases meal capacity).
            for line in apparel_lines:
                ProductVariant.objects.filter(pk=line.variant.pk).update(
                    stock=F("stock") - line.quantity
                )

    except _OutOfStock as exc:
        messages.error(request, str(exc))
        return redirect("cart")

    logger.info(
        "Order %s created (%s, %s %s)",
        order.order_number,
        order.payment_method,
        order.total_price,
        order.currency,
    )

    if order.has_meal_items:
        meals_notifications.notify(
            "order_received", to_email=order.email, name=order.full_name, ref=order.order_number,
        )

    # PlaceAnOrder marks intent, and fires for cash and card alike. The paid
    # conversion (CompletePayment) is reported separately, only once money
    # actually arrives.
    tiktok.send_event(
        tiktok.PLACE_AN_ORDER,
        request=request,
        properties=tiktok.order_properties(order),
        user=tiktok.user_payload(
            request,
            email=order.email,
            phone=order.phone,
            external_id=order.order_number,
        ),
    )

    if order.payment_method == Order.PaymentMethod.CASH:
        # A cash order is never "paid" online, so it would otherwise never
        # trigger a notification and the owner would not know about it.
        if settings.NOTIFY_ON_COD_ORDER:
            notifications.send_order_notification(order, reason="cod")
        cart.clear()
        return redirect(order.get_absolute_url())

    return _start_online_payment(request, cart, order)


def _start_online_payment(request, cart, order, *, retry_redirect="checkout"):
    """Create a Fawaterk invoice and redirect the customer to it.

    ``cart`` is optional: meals/views.py reuses this for subscription
    payments, which have no cart to clear (see meals/views.py::_start_subscription_payment).
    """
    if not fawaterk.is_configured():
        order.mark_failed("Online payment is not configured.")
        order.release_stock()
        messages.error(
            request,
            "Card payment is unavailable right now. Choose cash on delivery, "
            "or contact us on WhatsApp and we will take the order manually.",
        )
        return redirect(retry_redirect)

    return_kwargs = {
        "order_number": order.order_number,
        "token": order.access_token,
    }

    try:
        invoice = fawaterk.create_invoice(
            order,
            success_url=_absolute(request, reverse("payment_success", kwargs=return_kwargs)),
            fail_url=_absolute(request, reverse("payment_failed", kwargs=return_kwargs)),
            pending_url=_absolute(request, reverse("payment_pending", kwargs=return_kwargs)),
            webhook_url=_absolute(request, reverse("fawaterk_webhook")),
        )
    except fawaterk.FawaterkError as exc:
        logger.error("Fawaterk invoice failed for %s: %s", order.order_number, exc)
        order.mark_failed(str(exc))
        order.release_stock()
        messages.error(
            request,
            "We could not open the payment page. Nothing has been charged. "
            "Please try again, or choose cash on delivery.",
        )
        return redirect(retry_redirect)

    order.fawaterk_invoice_id = invoice["invoice_id"]
    order.fawaterk_invoice_key = invoice["invoice_key"]
    order.save(update_fields=["fawaterk_invoice_id", "fawaterk_invoice_key", "updated_at"])

    # The cart is cleared once the invoice exists: the order now holds the
    # items, and leaving the cart populated invites a duplicate order.
    if cart is not None:
        cart.clear()

    return redirect(invoice["url"])


# ---------------------------------------------------------------------------
# Order and payment return pages
# ---------------------------------------------------------------------------

def _get_order(order_number, token):
    """Fetch an order by number *and* token, so the URL cannot be guessed."""
    order = (
        Order.objects.filter(order_number=order_number, access_token=token)
        .prefetch_related("items")
        .first()
    )
    if not order:
        raise Http404("Order not found.")
    return order


@never_cache
@require_GET
def order_detail(request, order_number, token):
    order = _get_order(order_number, token)

    return render(
        request,
        "store/order_detail.html",
        {
            "order": order,
            "items": order.items.all(),
            "page_title": f"Order {order.order_number}",
        },
    )


@never_cache
@require_GET
def payment_success(request, order_number, token):
    """Landing page after a successful payment.

    The redirect itself is not proof of payment: a customer can bookmark or
    hand-edit this URL. So we confirm against the gateway before showing a paid
    receipt, and fall back to "pending" if we cannot confirm. The webhook is
    what usually marks the order paid; this is the belt to its braces.
    """
    order = _get_order(order_number, token)

    if not order.is_paid and order.fawaterk_invoice_id:
        paid, data = fawaterk.is_invoice_paid(order.fawaterk_invoice_id)
        if paid:
            newly_paid = order.mark_paid(
                invoice_id=order.fawaterk_invoice_id,
                method=(data or {}).get("payment_method", ""),
            )
            if newly_paid:
                logger.info(
                    "Order %s confirmed paid via success redirect", order.order_number
                )
                _on_payment_confirmed(order, request=request)

    return render(
        request,
        "store/payment_result.html",
        {
            "order": order,
            "items": order.items.all(),
            "outcome": "success" if order.is_paid else "pending",
            "page_title": "Payment received" if order.is_paid else "Payment pending",
        },
    )


@never_cache
@require_GET
def payment_failed(request, order_number, token):
    order = _get_order(order_number, token)

    # Only touch the order if the gateway has not already confirmed payment.
    if not order.is_paid and order.payment_status == Order.PaymentStatus.PENDING:
        order.mark_failed("Payment was cancelled or declined.")
        order.release_stock()

    return render(
        request,
        "store/payment_result.html",
        {
            "order": order,
            "items": order.items.all(),
            "outcome": "failed",
            "page_title": "Payment not completed",
        },
    )


@never_cache
@require_GET
def payment_pending(request, order_number, token):
    """Used for Fawry / Aman style codes that are paid later at an outlet."""
    order = _get_order(order_number, token)

    return render(
        request,
        "store/payment_result.html",
        {
            "order": order,
            "items": order.items.all(),
            "outcome": "pending",
            "page_title": "Payment pending",
        },
    )


@require_POST
def retry_payment(request, order_number, token):
    """Open a fresh invoice for an order whose first attempt failed."""
    order = _get_order(order_number, token)

    if order.is_paid:
        return redirect(order.get_absolute_url())
    if order.payment_method != Order.PaymentMethod.ONLINE:
        return redirect(order.get_absolute_url())

    # Re-reserve stock, since a failed attempt released it.
    for item in order.items.all():
        if item.variant_id:
            variant = ProductVariant.objects.filter(pk=item.variant_id).first()
            if not variant or variant.stock < item.quantity:
                messages.error(
                    request,
                    f"{item.product_name} ({item.size}) has sold out. "
                    "Please start a new order.",
                )
                return redirect("store")

    with transaction.atomic():
        for item in order.items.all():
            if item.variant_id:
                ProductVariant.objects.filter(pk=item.variant_id).update(
                    stock=F("stock") - item.quantity
                )
        order.payment_status = Order.PaymentStatus.PENDING
        order.payment_error = ""
        order.save(update_fields=["payment_status", "payment_error", "updated_at"])

    return _start_online_payment(request, get_cart(request), order)


# ---------------------------------------------------------------------------
# Fawaterk webhook
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def fawaterk_webhook(request):
    """Receive payment notifications from Fawaterk.

    Notes for whoever maintains this next:

    * The URL must contain ``_json`` for Fawaterk to send a JSON body. It sends
      form-encoded data otherwise, so both are handled here.
    * ``csrf_exempt`` is required: the gateway has no CSRF token. Authenticity
      comes from the HMAC instead, which is checked before anything is trusted.
    * We always answer 200 for a payload we understood, even if we could not
      match an order. A non-2xx makes Fawaterk retry the same event forever.
    * Every callback is written to WebhookEvent first, so a signature mismatch
      or an unmatched invoice can be investigated after the fact.
    """
    payload = _parse_webhook_body(request)
    if payload is None:
        return JsonResponse({"status": "error", "message": "Invalid payload"}, status=400)

    kind = _classify_webhook(payload)
    invoice_id = str(payload.get("invoice_id") or "")

    if kind == "fawaterk_expired":
        signature_valid = fawaterk.verify_expired_hash(payload)
    elif kind == "fawaterk_refund":
        # Refund callbacks carry no hashKey in the documented payload, so
        # they cannot be HMAC-verified like the others. As a mitigation, the
        # refund webhook URL configured on the Fawaterk dashboard must
        # include a long random ?token= that only we know; without a match
        # here the callback is not trusted enough to change an order's
        # payment status.
        expected_token = settings.FAWATERK_REFUND_WEBHOOK_TOKEN
        signature_valid = bool(expected_token) and hmac.compare_digest(
            expected_token, request.GET.get("token", "")
        )
    else:
        signature_valid = fawaterk.verify_invoice_hash(payload)

    event = WebhookEvent.objects.create(
        source=kind,
        invoice_id=invoice_id,
        payload=payload,
        signature_valid=signature_valid,
    )

    if settings.FAWATERK_VERIFY_WEBHOOK and not signature_valid:
        event.message = "Signature mismatch, ignored"
        event.save(update_fields=["message"])
        logger.warning(
            "Rejected Fawaterk webhook with bad signature (invoice=%s)", invoice_id
        )
        return JsonResponse({"status": "error", "message": "Invalid signature"}, status=403)

    order = _find_order(payload)
    if not order:
        event.message = "No matching order"
        event.save(update_fields=["message"])
        logger.warning("Fawaterk webhook had no matching order: %s", payload)
        return JsonResponse({"status": "ignored", "message": "No matching order"})

    event.order = order

    if kind == "fawaterk_paid":
        newly_paid = order.mark_paid(
            invoice_id=payload.get("invoice_id", ""),
            invoice_key=payload.get("invoice_key", ""),
            method=payload.get("payment_method", ""),
            reference=str(payload.get("referenceNumber") or ""),
        )
        event.processed = True
        event.message = "Order marked paid" if newly_paid else "Already paid, ignored"
        if newly_paid:
            logger.info("Order %s marked paid by webhook", order.order_number)
            # request is not forwarded, so the IP and user agent are omitted
            # rather than wrongly attributed to Fawaterk's server.
            _on_payment_confirmed(order, request=None)

    elif kind in {"fawaterk_failed", "fawaterk_expired"}:
        reason = (
            payload.get("errorMessage")
            or payload.get("status")
            or "Payment failed"
        )
        status = (
            Order.PaymentStatus.CANCELLED
            if kind == "fawaterk_expired"
            else Order.PaymentStatus.FAILED
        )
        changed = order.mark_failed(str(reason), status=status)
        if changed:
            order.release_stock()
        event.processed = True
        event.message = f"Order marked {status}" if changed else "Already paid, ignored"

    elif kind == "fawaterk_refund":
        if str(payload.get("status", "")).lower() == "approved":
            order.payment_status = Order.PaymentStatus.REFUNDED
            order.save(update_fields=["payment_status", "updated_at"])
            event.processed = True
            event.message = "Order marked refunded"

    event.save(update_fields=["order", "processed", "message"])
    return JsonResponse({"status": "success"})


def _parse_webhook_body(request):
    """Accept a JSON body or form-encoded fields."""
    content_type = (request.content_type or "").lower()

    if "application/json" in content_type:
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            logger.warning("Fawaterk webhook body was not valid JSON")
            return None
        return data if isinstance(data, dict) else None

    if request.POST:
        data = request.POST.dict()
        # pay_load arrives as a JSON string when form-encoded.
        raw = data.get("pay_load")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                data["pay_load"] = json.loads(raw)
            except ValueError:
                pass
        return data

    # Some senders post JSON without the header.
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        return data if isinstance(data, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _classify_webhook(payload):
    """Work out which of Fawaterk's four callback shapes this is."""
    status = str(payload.get("invoice_status") or "").lower()

    if status == "paid":
        return "fawaterk_paid"
    if str(payload.get("status") or "").upper() == "EXPIRED":
        return "fawaterk_expired"
    if payload.get("errorMessage") or str(payload.get("status") or "").lower() in {
        "failed",
        "declined",
    }:
        return "fawaterk_failed"
    if payload.get("approvedAt") or payload.get("reason"):
        return "fawaterk_refund"
    if payload.get("invoice_id"):
        # A paid callback that omitted invoice_status.
        return "fawaterk_paid"
    return "unknown"


def _find_order(payload):
    """Match a callback to an order.

    Preference order: our own order number echoed back in ``pay_load`` (the
    value we set ourselves), then the invoice id, then the invoice key.
    """
    pay_load = payload.get("pay_load")
    if isinstance(pay_load, str):
        try:
            pay_load = json.loads(pay_load)
        except ValueError:
            pay_load = None

    if isinstance(pay_load, dict):
        order_number = pay_load.get("order_number")
        if order_number:
            order = Order.objects.filter(order_number=order_number).first()
            if order:
                return order

    invoice_id = payload.get("invoice_id")
    if invoice_id:
        order = Order.objects.filter(fawaterk_invoice_id=str(invoice_id)).first()
        if order:
            return order

    invoice_key = payload.get("invoice_key")
    if invoice_key:
        return Order.objects.filter(fawaterk_invoice_key=invoice_key).first()

    return None
