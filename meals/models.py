"""Data model for HanyFit Meals.

Deliberately kept in its own app instead of being folded into ``store``: it
has its own product catalogue (Meal), its own capacity/availability concept
(daily meal capacity, not clothing-style size stock), and its own
subscription lifecycle that a plain Order was never designed to hold. Where
the existing store infrastructure already does the job -- Cart, Order,
OrderItem, checkout, Fawaterk payment, the dashboard permission -- this app
reuses it rather than duplicating it (see store/models.py OrderItem.meal /
item_type / Order.delivery_date / Order.subscription, and store/cart.py's
meal line support).

Import-wise this app depends on ``store`` (Meal orders become store.Order /
store.OrderItem rows, and a paid subscription is fulfilled through a real
store.Order too -- see Order.subscription). ``store`` in turn takes a few
narrow, optional string FKs back into this app (Order.delivery_slot,
Order.coupon, Order.subscription). That back-reference is a deliberate
exception to "meals depends on store, not the other way round" -- seemed
cleaner than a parallel MealOrder table. It is called out again in
store/models.py next to the fields themselves and in the implementation
report.
"""

import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from .choices import PLAN_DURATION_DAYS, Goal, MealType, PlanType

ZERO = Decimal("0.00")


def resolve_image(value):
    """Same behaviour as store.models.resolve_image, kept local on purpose.

    Duplicating this ~10 line function avoids a meals -> store import for a
    single helper; everything that actually needs to be shared (Order,
    OrderItem) is wired the other way already.
    """
    if not value:
        return ""
    value = value.strip()
    if value.startswith(("http://", "https://", "//", "/")):
        return value
    try:
        return static(value)
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Meal catalogue
# ---------------------------------------------------------------------------


class MealQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_goal(self, goal):
        if not goal:
            return self
        return self.filter(goal=goal)

    def for_meal_type(self, meal_type):
        if not meal_type:
            return self
        return self.filter(meal_type=meal_type)

    def under_calories(self, limit):
        if not limit:
            return self
        return self.filter(calories__lte=limit)

    def high_protein(self, threshold=None):
        from django.conf import settings

        threshold = threshold or getattr(
            settings, "MEALS_HIGH_PROTEIN_THRESHOLD_G", 25
        )
        return self.filter(protein_g__gte=threshold)


class Meal(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=220)
    description = models.TextField(blank=True)

    image = models.CharField(
        max_length=500,
        blank=True,
        help_text="Static path (images/meals/chicken-rice.jpg) or full https:// URL.",
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
        help_text="Price in EGP. Meals are Egypt-only in this phase.",
    )

    calories = models.PositiveIntegerField()
    protein_g = models.DecimalField(max_digits=6, decimal_places=1)
    carbs_g = models.DecimalField(max_digits=6, decimal_places=1)
    fat_g = models.DecimalField(max_digits=6, decimal_places=1)
    ingredients = models.TextField(blank=True)

    goal = models.CharField(max_length=20, choices=Goal.choices, db_index=True)
    meal_type = models.CharField(
        max_length=20, choices=MealType.choices, db_index=True
    )

    daily_capacity = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Maximum units of this meal that can be delivered on a single "
            "day. 0 means unlimited. See MealDailyAvailability for "
            "per-date overrides and live reservation counts."
        ),
    )

    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MealQuerySet.as_manager()

    class Meta:
        ordering = ["display_order", "name"]
        indexes = [
            models.Index(fields=["goal", "meal_type", "is_active"]),
            models.Index(fields=["is_active", "calories"]),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("meals:meal_detail", kwargs={"slug": self.slug})

    @property
    def image_url(self):
        return resolve_image(self.image)

    @property
    def is_unlimited(self):
        """True when this meal has no default daily cap (daily_capacity == 0).

        A per-date MealDailyAvailability override can still close an
        otherwise-unlimited meal for one specific day.
        """
        return self.daily_capacity == 0

    @property
    def average_rating(self):
        """None when there are no visible reviews yet -- templates check
        this rather than showing "0.0 stars" for an unreviewed meal."""
        from django.db.models import Avg

        result = self.reviews.filter(is_hidden=False).aggregate(avg=Avg("rating"))
        return result["avg"]

    @property
    def review_count(self):
        return self.reviews.filter(is_hidden=False).count()


class MealDailyAvailability(models.Model):
    """Per-date capacity override + live reservation counter for a Meal.

    Rows are created lazily (get-or-create) the first time a date is
    actually reserved against or overridden from the dashboard -- the table
    is not pre-populated for every meal x future date, which would grow
    unbounded for no benefit.
    """

    meal = models.ForeignKey(
        Meal, on_delete=models.CASCADE, related_name="daily_availability"
    )
    date = models.DateField(db_index=True)

    capacity_override = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Overrides the meal's default daily capacity for this date only.",
    )
    is_closed = models.BooleanField(
        default=False,
        help_text="Mark this meal unavailable for this date regardless of capacity.",
    )
    reserved = models.PositiveIntegerField(default=0, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meal", "date"], name="unique_meal_availability_per_date"
            )
        ]
        indexes = [models.Index(fields=["meal", "date"])]

    def __str__(self):
        return f"{self.meal.name} — {self.date.isoformat()}"

    @property
    def effective_capacity(self):
        """0 (or the meal default) means unlimited."""
        if self.capacity_override is not None:
            return self.capacity_override
        return self.meal.daily_capacity

    @property
    def remaining(self):
        if self.is_closed:
            return 0
        cap = self.effective_capacity
        if cap == 0:
            return None  # unlimited
        return max(cap - self.reserved, 0)

    @property
    def is_available(self):
        remaining = self.remaining
        return not self.is_closed and (remaining is None or remaining > 0)


# ---------------------------------------------------------------------------
# Delivery scheduling (shared by one-off meal orders and subscriptions)
# ---------------------------------------------------------------------------


class DeliverySlot(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    weekday = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    cutoff_hours_before = models.PositiveIntegerField(
        default=0,
        help_text="Hours before start_time after which this slot can no longer be booked. 0 = no cutoff.",
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["weekday", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["weekday", "start_time", "end_time"],
                name="unique_delivery_slot_window",
            )
        ]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

    def is_bookable_for(self, date):
        """Weekday match + still before the cutoff, relative to now."""
        if not self.is_active:
            return False
        if date.weekday() != self.weekday:
            return False
        if self.cutoff_hours_before:
            deadline = timezone.make_aware(
                datetime.combine(date, self.start_time)
            ) - timedelta(hours=self.cutoff_hours_before)
            if timezone.now() >= deadline:
                return False
        return True


class DeliveryClosedDate(models.Model):
    """A whole-date override: no deliveries at all that day (public holiday,
    kitchen closed, etc.), regardless of what MealDailyAvailability or
    DeliverySlot would otherwise allow. Checked by both the one-off meal
    checkout (store/views.py) and subscription skip/reschedule
    (services/subscriptions.py) through is_delivery_open().
    """

    date = models.DateField(unique=True, db_index=True)
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date.isoformat()}" + (f" ({self.reason})" if self.reason else "")


# ---------------------------------------------------------------------------
# Coupons -- meals & subscriptions only in this phase (see services/coupons.py)
# ---------------------------------------------------------------------------


class Coupon(models.Model):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed amount (EGP)"

    class AppliesTo(models.TextChoices):
        MEALS = "meals", "Meals only"
        SUBSCRIPTIONS = "subscriptions", "Subscriptions only"
        BOTH = "both", "Meals & subscriptions"

    code = models.CharField(max_length=40, unique=True)
    is_active = models.BooleanField(default=True)

    discount_type = models.CharField(
        max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENT
    )
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Caps the discount for percentage coupons. Ignored for fixed-amount coupons.",
    )
    min_subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)

    applies_to = models.CharField(
        max_length=20, choices=AppliesTo.choices, default=AppliesTo.BOTH
    )

    expires_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Total redemptions allowed. Blank = unlimited."
    )
    per_customer_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Redemptions allowed per phone number. Blank = unlimited."
    )
    times_used = models.PositiveIntegerField(default=0, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def is_valid_now(self):
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() >= self.expires_at:
            return False
        if self.usage_limit is not None and self.times_used >= self.usage_limit:
            return False
        return True

    def applies_to_meals(self):
        return self.applies_to in (self.AppliesTo.MEALS, self.AppliesTo.BOTH)

    def applies_to_subscriptions(self):
        return self.applies_to in (self.AppliesTo.SUBSCRIPTIONS, self.AppliesTo.BOTH)

    def applies_to_kind(self, kind):
        """``kind`` is "meals" or "subscriptions" (see services/coupons.py)."""
        if kind == "subscriptions":
            return self.applies_to_subscriptions()
        return self.applies_to_meals()

    def calculate_discount(self, subtotal):
        """Backend-authoritative discount for a given subtotal. Never negative,
        never larger than the subtotal itself."""
        subtotal = Decimal(subtotal)
        if subtotal <= ZERO or subtotal < self.min_subtotal:
            return ZERO
        if self.discount_type == self.DiscountType.PERCENT:
            amount = (subtotal * self.discount_value / Decimal("100")).quantize(ZERO)
            if self.max_discount_amount is not None:
                amount = min(amount, self.max_discount_amount)
        else:
            amount = self.discount_value
        return max(min(amount, subtotal), ZERO)


class CouponRedemption(models.Model):
    """One row per successful use. Lets us enforce per-customer limits and
    keeps an audit trail independent of Order/Subscription retention."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    phone = models.CharField(max_length=30, db_index=True)
    order = models.ForeignKey(
        "store.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="coupon_redemptions"
    )
    subscription = models.ForeignKey(
        "Subscription", on_delete=models.SET_NULL, null=True, blank=True, related_name="coupon_redemptions"
    )
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class SubscriptionPlanPrice(models.Model):
    """Backend-authoritative price for one (plan_type, goal, meals_per_day)
    combination. The dashboard/admin edits these directly -- nothing about a
    subscription's price is ever computed from a guessed formula."""

    plan_type = models.CharField(max_length=10, choices=PlanType.choices)
    goal = models.CharField(max_length=20, choices=Goal.choices)
    meals_per_day = models.PositiveSmallIntegerField(default=1)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
        help_text="Total price in EGP for the whole plan duration (7 or 30 days).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan_type", "goal", "meals_per_day"],
                name="unique_plan_price_combo",
            )
        ]
        ordering = ["plan_type", "goal", "meals_per_day"]

    def __str__(self):
        return f"{self.get_plan_type_display()} / {self.get_goal_display()} / {self.meals_per_day}/day"


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash on delivery"
        ONLINE = "online", "Online payment"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    # Guest identity -- mirrors store.Order, no customer-account system exists
    # or is being introduced here (explicit decision, see chat history).
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, db_index=True)
    email = models.EmailField(blank=True)

    plan_type = models.CharField(max_length=10, choices=PlanType.choices)
    goal = models.CharField(max_length=20, choices=Goal.choices)
    meals_per_day = models.PositiveSmallIntegerField(default=1)

    # Empty list = deliver every day of the plan (the original behaviour,
    # kept as the default so existing subscriptions/tests are unaffected).
    # Otherwise a list of ints, 0=Monday..6=Sunday (see choices.WEEKDAY_CHOICES),
    # and only those weekdays get a SubscriptionDelivery row.
    delivery_weekdays = models.JSONField(default=list, blank=True)

    # Nutrition preferences captured at signup. Free-text on purpose -- there
    # is no allergen/ingredient database in this project to match against
    # structurally, so meal_recommendation.py does a best-effort keyword
    # exclusion against Meal.ingredients/name (see that module's docstring
    # for the exact limitation). Shown as-is to staff on the dashboard so a
    # human makes the final call on what actually gets packed.
    allergies = models.TextField(blank=True)
    disliked_foods = models.TextField(blank=True)
    dietary_notes = models.TextField(blank=True)

    start_date = models.DateField()
    end_date = models.DateField()

    delivery_slot = models.ForeignKey(
        DeliverySlot, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscriptions"
    )
    governorate = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100)
    address = models.TextField()
    notes = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    fawaterk_invoice_id = models.CharField(max_length=64, blank=True, db_index=True)
    fawaterk_invoice_key = models.CharField(max_length=128, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="EGP")

    coupon = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscriptions"
    )
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)

    access_token = models.CharField(max_length=48, editable=False, db_index=True)

    # Set when this subscription was created by renewing an earlier one (see
    # services/subscriptions.py::renew). Self-FK, not a new concept -- lets
    # the dashboard show a renewal chain instead of unrelated rows.
    renewed_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="renewals"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "start_date"])]

    def __str__(self):
        return f"{self.full_name} — {self.get_plan_type_display()} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.access_token:
            self.access_token = secrets.token_urlsafe(24)
        if not self.end_date and self.start_date:
            self.end_date = self.start_date + timedelta(
                days=PLAN_DURATION_DAYS[self.plan_type] - 1
            )
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse(
            "meals:subscription_status",
            kwargs={"pk": self.pk, "token": self.access_token},
        )

    @property
    def total_price(self):
        return max(self.price - self.discount_amount, ZERO)

    # Kept as an alias -- services/subscriptions.py and early drafts of this
    # code refer to the same figure as total_after_discount.
    total_after_discount = total_price

    @property
    def is_paid(self):
        return self.payment_status == self.PaymentStatus.PAID

    @property
    def delivery_weekdays_display(self):
        from .choices import WEEKDAY_CHOICES

        if not self.delivery_weekdays:
            return "Every day"
        labels = dict(WEEKDAY_CHOICES)
        return ", ".join(labels.get(day, str(day)) for day in self.delivery_weekdays)

    def mark_paid(self, *, invoice_id="", invoice_key=""):
        """Mirrors store.models.Order.mark_paid. Safe to call more than once;
        returns True only the first time so callers (store/views.py
        ``_on_payment_confirmed``) can trigger side effects exactly once."""
        if self.payment_status == self.PaymentStatus.PAID:
            return False
        self.payment_status = self.PaymentStatus.PAID
        self.paid_at = timezone.now()
        if invoice_id:
            self.fawaterk_invoice_id = str(invoice_id)
        if invoice_key:
            self.fawaterk_invoice_key = invoice_key
        self.save(
            update_fields=[
                "payment_status", "paid_at", "fawaterk_invoice_id",
                "fawaterk_invoice_key", "updated_at",
            ]
        )
        return True

    def mark_failed(self):
        if self.payment_status == self.PaymentStatus.PAID:
            return False
        self.payment_status = self.PaymentStatus.FAILED
        self.save(update_fields=["payment_status", "updated_at"])
        return True


class SubscriptionDelivery(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        PREPARING = "preparing", "Preparing"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        SKIPPED = "skipped", "Skipped"
        CANCELLED = "cancelled", "Cancelled"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="deliveries"
    )
    scheduled_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    # True only when this row was auto-skipped as a *side effect* of pausing
    # the whole subscription (see services/subscriptions.py::set_status).
    # Lets resume() know which SKIPPED rows to bring back to SCHEDULED,
    # without touching a day the customer skipped individually on purpose.
    auto_skipped = models.BooleanField(default=False)
    meals = models.ManyToManyField(Meal, blank=True, related_name="subscription_deliveries")
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "scheduled_date"],
                name="unique_subscription_delivery_per_date",
            )
        ]
        ordering = ["scheduled_date"]

    def __str__(self):
        return f"{self.subscription_id} — {self.scheduled_date.isoformat()}"


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


class Review(models.Model):
    """One review per OrderItem -- the OneToOne is what actually enforces
    "can't review the same purchased meal twice", not application code.

    A review can only be created for an OrderItem that is (a) a meal line,
    (b) on a paid order, so a customer can only review something they
    genuinely bought -- see services/reviews.py::create_review, which is the
    only place that is allowed to construct one.
    """

    order_item = models.OneToOneField(
        "store.OrderItem", on_delete=models.CASCADE, related_name="review"
    )
    meal = models.ForeignKey(Meal, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1 to 5.",
    )
    comment = models.TextField(blank=True)
    is_hidden = models.BooleanField(
        default=False, help_text="Hide from the public average/listing without deleting it."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["meal", "is_hidden"])]

    def __str__(self):
        return f"{self.meal.name} — {self.rating}/5"
