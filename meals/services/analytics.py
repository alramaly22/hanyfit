"""Read-only aggregate stats for the HanyFit Meals dashboard overview
(store/dashboard.py::meals_home, brief sections 18-19).

Pure queryset aggregation over the existing Order/OrderItem/Subscription
tables -- no new tracking/event table, per "don't duplicate tracking
infrastructure" in the brief. store.models.PageVisit already exists for
traffic analytics and is untouched; this module is about *sales*, not
visits.

"Revenue" here means the total_price of every meal order that is not
cancelled/failed -- including cash-on-delivery orders still awaiting
collection, since that money is genuinely expected. It is therefore an
"expected revenue" figure, not strictly "cash already collected"; the
dashboard template labels it that way.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from store.models import Order, OrderItem
from ..models import Subscription

ZERO = Decimal("0.00")

_LIVE_ORDER_FILTER = ~Q(order_status=Order.OrderStatus.CANCELLED) & ~Q(
    payment_status=Order.PaymentStatus.FAILED
)


def _meal_orders():
    return Order.objects.filter(
        items__item_type=OrderItem.ItemType.MEAL,
    ).filter(_LIVE_ORDER_FILTER).distinct()


def revenue_since(days):
    since = timezone.now() - timedelta(days=days)
    total = _meal_orders().filter(created_at__gte=since).aggregate(total=Sum("total_price"))["total"]
    return total or ZERO


def overview():
    orders = _meal_orders()
    order_count = orders.count()
    total_revenue = orders.aggregate(total=Sum("total_price"))["total"] or ZERO
    average_order_value = (total_revenue / order_count) if order_count else ZERO

    cancelled_count = Order.objects.filter(
        items__item_type=OrderItem.ItemType.MEAL, order_status=Order.OrderStatus.CANCELLED,
    ).distinct().count()

    subscriptions = Subscription.objects.all()
    subscription_count = subscriptions.count()
    active_subscriptions = subscriptions.filter(status=Subscription.Status.ACTIVE).count()
    cancelled_subscriptions = subscriptions.filter(status=Subscription.Status.CANCELLED).count()
    paid_subscriptions = subscriptions.filter(payment_status=Subscription.PaymentStatus.PAID).count()
    renewed_subscriptions = subscriptions.filter(renewed_from__isnull=False).count()

    return {
        "revenue_today": revenue_since(1),
        "revenue_7_days": revenue_since(7),
        "revenue_30_days": revenue_since(30),
        "total_revenue": total_revenue,
        "order_count": order_count,
        "cancelled_order_count": cancelled_count,
        "average_order_value": average_order_value,
        "best_selling_meals": best_selling_meals(),
        "goal_distribution": goal_distribution(),
        "popular_delivery_slots": popular_delivery_slots(),
        "subscription_count": subscription_count,
        "active_subscriptions": active_subscriptions,
        "subscription_conversion_rate": _percent(paid_subscriptions, subscription_count),
        "subscription_cancellation_rate": _percent(cancelled_subscriptions, subscription_count),
        "subscription_renewal_rate": _percent(renewed_subscriptions, subscription_count),
    }


def best_selling_meals(limit=5):
    return (
        OrderItem.objects.filter(item_type=OrderItem.ItemType.MEAL, order__in=_meal_orders())
        .values("meal__name")
        .annotate(units_sold=Sum("quantity"))
        .order_by("-units_sold")[:limit]
    )


def goal_distribution():
    """Meal *units* sold, grouped by the meal's goal -- which goal customers
    actually order, not just which goal has more meals listed."""
    return (
        OrderItem.objects.filter(item_type=OrderItem.ItemType.MEAL, order__in=_meal_orders())
        .values("meal__goal")
        .annotate(units_sold=Sum("quantity"))
        .order_by("-units_sold")
    )


def popular_delivery_slots(limit=5):
    """Combines one-off meal-order deliveries and subscription deliveries
    against the same DeliverySlot, since both draw from the same table."""
    from_orders = (
        _meal_orders()
        .exclude(delivery_slot__isnull=True)
        .values("delivery_slot__weekday", "delivery_slot__start_time", "delivery_slot__end_time")
        .annotate(count=Count("id"))
    )
    from_subscriptions = (
        Subscription.objects.exclude(delivery_slot__isnull=True)
        .values("delivery_slot__weekday", "delivery_slot__start_time", "delivery_slot__end_time")
        .annotate(count=Count("id"))
    )
    combined = {}
    for row in list(from_orders) + list(from_subscriptions):
        key = (row["delivery_slot__start_time"], row["delivery_slot__end_time"])
        combined[key] = combined.get(key, 0) + row["count"]
    ranked = sorted(combined.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [{"start_time": k[0], "end_time": k[1], "count": v} for k, v in ranked]


def _percent(part, total):
    if not total:
        return None
    return round((part / total) * 100, 1)
