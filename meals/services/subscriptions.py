"""Subscription pricing, creation, and the full self-service lifecycle
(pause/resume/skip/reschedule/change address/renew).

Price is never trusted from the frontend: ``calculate_price`` is the single
source of truth, looked up from SubscriptionPlanPrice (set from the
dashboard/admin), and is recomputed server-side both when a quote is shown
and again right before the subscription row is actually created.

Every mutating function here re-validates from scratch (ownership is proven
by the caller already having matched Subscription.pk + access_token -- see
meals/views.py -- object-level checks below are about *state*, not identity).
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..choices import PLAN_DURATION_DAYS
from ..models import DeliveryClosedDate, Subscription, SubscriptionDelivery, SubscriptionPlanPrice
from . import availability as availability_service
from . import coupons as coupons_service


class SubscriptionError(Exception):
    """User-facing error building, pricing, or managing a subscription."""


def calculate_price(plan_type, goal, meals_per_day):
    try:
        plan = SubscriptionPlanPrice.objects.get(
            plan_type=plan_type, goal=goal, meals_per_day=meals_per_day, is_active=True,
        )
    except SubscriptionPlanPrice.DoesNotExist:
        raise SubscriptionError(
            "This subscription plan is not available right now. Please choose a different option."
        )
    return plan.price


# Re-exported for anything (forms, dashboard) that already imports
# is_delivery_open from this module -- the real implementation now lives in
# availability.py, next to the rest of the delivery-availability logic.
is_delivery_open = availability_service.is_delivery_open


def _delivery_dates(start_date, duration, delivery_weekdays):
    """Calendar dates for a plan, filtered by the customer's chosen weekdays
    (empty/None = every day, the original behaviour) and by any
    DeliveryClosedDate. A closed date is simply skipped -- not moved -- so a
    30-day plan may end up with fewer than 30 delivery rows if a holiday
    falls inside it; the flat plan price is unaffected (see
    SubscriptionPlanPrice -- it prices the plan, not a per-delivery fee).
    """
    weekdays = set(delivery_weekdays) if delivery_weekdays else None
    closed = set(
        DeliveryClosedDate.objects.filter(
            date__gte=start_date, date__lt=start_date + timedelta(days=duration),
        ).values_list("date", flat=True)
    )
    dates = []
    for offset in range(duration):
        date = start_date + timedelta(days=offset)
        if weekdays is not None and date.weekday() not in weekdays:
            continue
        if date in closed:
            continue
        dates.append(date)
    return dates


@transaction.atomic
def create_subscription(*, full_name, phone, email, governorate, city, address, notes,
                         plan_type, goal, meals_per_day, start_date, delivery_slot,
                         payment_method, coupon_code=None, delivery_weekdays=None,
                         allergies="", disliked_foods="", dietary_notes="",
                         renewed_from=None):
    """Validate pricing/coupon server-side and create the Subscription +
    its full SubscriptionDelivery schedule in one transaction.
    """
    price = calculate_price(plan_type, goal, meals_per_day)
    duration = PLAN_DURATION_DAYS[plan_type]
    end_date = start_date + timedelta(days=duration - 1)
    delivery_weekdays = delivery_weekdays or []

    dates = _delivery_dates(start_date, duration, delivery_weekdays)
    if not dates:
        raise SubscriptionError(
            "No deliveries are possible in that window -- try a different start date or delivery days."
        )

    discount_amount = 0
    coupon = None
    if coupon_code:
        coupon = coupons_service.validate(
            coupon_code, subtotal=price, phone=phone, kind="subscriptions",
        )

    subscription = Subscription.objects.create(
        full_name=full_name, phone=phone, email=email,
        governorate=governorate, city=city, address=address, notes=notes,
        plan_type=plan_type, goal=goal, meals_per_day=meals_per_day,
        delivery_weekdays=delivery_weekdays,
        allergies=allergies, disliked_foods=disliked_foods, dietary_notes=dietary_notes,
        start_date=start_date, end_date=end_date, delivery_slot=delivery_slot,
        price=price, payment_method=payment_method, renewed_from=renewed_from,
    )

    if coupon:
        discount_amount = coupons_service.redeem(
            coupon.pk, subtotal=price, phone=phone, subscription=subscription,
        )
        subscription.coupon = coupon
        subscription.discount_amount = discount_amount
        subscription.save(update_fields=["coupon", "discount_amount"])

    SubscriptionDelivery.objects.bulk_create(
        [SubscriptionDelivery(subscription=subscription, scheduled_date=date) for date in dates]
    )

    return subscription


def set_status(subscription, status):
    """Update status. Pausing/cancelling skips every not-yet-handled
    delivery; resuming brings back only the ones that were auto-skipped by a
    *previous* pause (a day the customer skipped individually stays skipped).
    """
    previous_status = subscription.status
    subscription.status = status
    subscription.save(update_fields=["status", "updated_at"])

    if status in (Subscription.Status.PAUSED, Subscription.Status.CANCELLED):
        target = (
            SubscriptionDelivery.Status.SKIPPED
            if status == Subscription.Status.PAUSED
            else SubscriptionDelivery.Status.CANCELLED
        )
        subscription.deliveries.filter(
            status=SubscriptionDelivery.Status.SCHEDULED,
        ).update(status=target, auto_skipped=(status == Subscription.Status.PAUSED))

    elif status == Subscription.Status.ACTIVE and previous_status == Subscription.Status.PAUSED:
        subscription.deliveries.filter(
            status=SubscriptionDelivery.Status.SKIPPED,
            auto_skipped=True,
            scheduled_date__gte=timezone.localdate(),
        ).update(status=SubscriptionDelivery.Status.SCHEDULED, auto_skipped=False)


def pause(subscription):
    if subscription.status != Subscription.Status.ACTIVE:
        raise SubscriptionError("Only an active subscription can be paused.")
    set_status(subscription, Subscription.Status.PAUSED)


def resume(subscription):
    if subscription.status != Subscription.Status.PAUSED:
        raise SubscriptionError("Only a paused subscription can be resumed.")
    set_status(subscription, Subscription.Status.ACTIVE)


def cancel(subscription):
    if subscription.status in (Subscription.Status.CANCELLED, Subscription.Status.EXPIRED):
        raise SubscriptionError("This subscription is already inactive.")
    set_status(subscription, Subscription.Status.CANCELLED)


def skip_delivery(delivery):
    """Customer skips one specific upcoming delivery (not the whole plan)."""
    if delivery.status != SubscriptionDelivery.Status.SCHEDULED:
        raise SubscriptionError("Only a scheduled delivery can be skipped.")
    if delivery.scheduled_date <= timezone.localdate():
        raise SubscriptionError("It's too late to skip today's delivery.")
    delivery.status = SubscriptionDelivery.Status.SKIPPED
    delivery.auto_skipped = False
    delivery.save(update_fields=["status", "auto_skipped", "updated_at"])


def reschedule_delivery(delivery, new_date):
    """Move one delivery to a different date, inside the same subscription's
    window, respecting closed dates and the customer's own chosen weekdays."""
    subscription = delivery.subscription

    if delivery.status != SubscriptionDelivery.Status.SCHEDULED:
        raise SubscriptionError("Only a scheduled delivery can be rescheduled.")
    if delivery.scheduled_date <= timezone.localdate():
        raise SubscriptionError("It's too late to reschedule today's delivery.")
    if new_date <= timezone.localdate():
        raise SubscriptionError("Choose a date in the future.")
    if not (subscription.start_date <= new_date <= subscription.end_date):
        raise SubscriptionError("Choose a date inside the subscription's active window.")
    if subscription.delivery_weekdays and new_date.weekday() not in subscription.delivery_weekdays:
        raise SubscriptionError("That date isn't one of your chosen delivery days.")
    if not is_delivery_open(new_date):
        raise SubscriptionError("Deliveries are closed on that date. Please choose another.")
    if subscription.deliveries.filter(scheduled_date=new_date).exclude(pk=delivery.pk).exists():
        raise SubscriptionError("You already have a delivery scheduled that day.")

    delivery.scheduled_date = new_date
    delivery.save(update_fields=["scheduled_date", "updated_at"])
    return delivery


def change_address(subscription, *, governorate, city, address):
    if subscription.status == Subscription.Status.CANCELLED:
        raise SubscriptionError("This subscription is cancelled; start a new one instead.")
    subscription.governorate = governorate
    subscription.city = city
    subscription.address = address
    subscription.save(update_fields=["governorate", "city", "address", "updated_at"])
    return subscription


@transaction.atomic
def renew(subscription, *, start_date=None, coupon_code=None):
    """Create a brand-new Subscription that carries over this one's plan and
    preferences, priced fresh from the current SubscriptionPlanPrice (prices
    may have changed since the original signup). Linked via renewed_from so
    the dashboard can show the chain instead of two unrelated rows.
    """
    if subscription.status not in (Subscription.Status.ACTIVE, Subscription.Status.EXPIRED):
        raise SubscriptionError("Only an active or expired subscription can be renewed.")

    start_date = start_date or (subscription.end_date + timedelta(days=1))
    if start_date <= subscription.end_date:
        raise SubscriptionError("The renewal must start after the current plan ends.")

    return create_subscription(
        full_name=subscription.full_name, phone=subscription.phone, email=subscription.email,
        governorate=subscription.governorate, city=subscription.city, address=subscription.address,
        notes=subscription.notes,
        plan_type=subscription.plan_type, goal=subscription.goal, meals_per_day=subscription.meals_per_day,
        delivery_weekdays=subscription.delivery_weekdays,
        allergies=subscription.allergies, disliked_foods=subscription.disliked_foods,
        dietary_notes=subscription.dietary_notes,
        start_date=start_date, delivery_slot=subscription.delivery_slot,
        payment_method=subscription.payment_method, coupon_code=coupon_code,
        renewed_from=subscription,
    )
