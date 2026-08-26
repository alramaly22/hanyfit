"""Daily meal capacity: checking, reserving and releasing.

Mirrors the pattern already used for apparel stock in
``store/views.py::_place_order`` (row-level lock + atomic update), but keyed
by (meal, date) instead of (variant,) since a meal has no fixed inventory --
only a per-delivery-day ceiling.
"""

from django.db import transaction

from ..models import DeliveryClosedDate, Meal, MealDailyAvailability


class MealUnavailable(Exception):
    """Raised when a meal cannot fulfil the requested quantity for a date."""

    def __init__(self, meal, date, remaining):
        self.meal = meal
        self.date = date
        self.remaining = remaining
        super().__init__(
            f"{meal.name} is not available for {remaining if remaining else 0} "
            f"unit(s) on {date.isoformat()}"
        )


def is_delivery_open(date):
    """False if the business has closed this whole date to deliveries (see
    DeliveryClosedDate / the admin's "Delivery closed dates", Phase 17).
    Checked by both the one-off meal checkout (store/forms.py) and
    subscription creation/reschedule (services/subscriptions.py).
    """
    return not DeliveryClosedDate.objects.filter(date=date).exists()


def remaining_for(meal, date):
    """Read-only: units still sellable for ``meal`` on ``date``. None = unlimited.

    Does not create a database row -- safe to call on every catalogue/detail
    page render.
    """
    try:
        row = meal.daily_availability.get(date=date)
    except MealDailyAvailability.DoesNotExist:
        return None if meal.is_unlimited else meal.daily_capacity
    return row.remaining


def is_available(meal, date, quantity=1):
    remaining = remaining_for(meal, date)
    return remaining is None or remaining >= quantity


@transaction.atomic
def reserve(meal_id, date, quantity):
    """Atomically reserve ``quantity`` units of ``meal_id`` for ``date``.

    Locks (and creates if missing) the MealDailyAvailability row for this
    exact (meal, date) pair, then raises MealUnavailable if there is not
    enough remaining capacity. Called from inside the same order-creation
    transaction as the apparel stock lock, so either the whole order commits
    or none of it does.
    """
    meal = Meal.objects.select_for_update().get(pk=meal_id)

    if meal.is_unlimited:
        row, _ = MealDailyAvailability.objects.select_for_update().get_or_create(
            meal=meal, date=date,
        )
        if row.is_closed:
            raise MealUnavailable(meal, date, 0)
        row.reserved = row.reserved + quantity
        row.save(update_fields=["reserved", "updated_at"])
        return

    row, _ = MealDailyAvailability.objects.select_for_update().get_or_create(
        meal=meal, date=date,
    )
    remaining = row.remaining
    if remaining is not None and remaining < quantity:
        raise MealUnavailable(meal, date, remaining)
    row.reserved = row.reserved + quantity
    row.save(update_fields=["reserved", "updated_at"])


def release(meal_id, date, quantity):
    """Undo a reservation (payment failed / order cancelled)."""
    MealDailyAvailability.objects.filter(meal_id=meal_id, date=date).update(
        reserved=_clamp_decrement(quantity)
    )


def _clamp_decrement(quantity):
    from django.db.models import F, Value
    from django.db.models.functions import Greatest

    return Greatest(F("reserved") - quantity, Value(0))


def release_order_reservations(order):
    """Release every meal reservation belonging to ``order``.

    Used by ``store.models.Order.release_stock()`` when a payment fails or
    an order is cancelled.
    """
    if not order.delivery_date:
        return
    for item in order.items.filter(item_type="meal"):
        if item.meal_id:
            release(item.meal_id, order.delivery_date, item.quantity)
