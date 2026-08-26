"""Meal review creation.

The only entry point that is allowed to construct a Review -- everything a
review needs to be legitimate (really this customer's order, really a meal,
really paid) is checked here once instead of trusted from a form.
"""

from ..models import Review


class ReviewError(Exception):
    """User-facing error creating a review."""


def create_review(order_item, *, rating, comment=""):
    from store.models import Order, OrderItem  # local import: store depends on nothing here, avoid a cycle at module load

    if order_item.item_type != OrderItem.ItemType.MEAL or order_item.meal_id is None:
        raise ReviewError("Only meals can be reviewed.")
    # Not order.is_paid: cash-on-delivery orders (the main payment method for
    # this market) never get payment_status flipped to paid anywhere in the
    # existing dashboard -- only order_status is tracked for them, through to
    # Delivered. Gating on "delivered" is also just a more literal match for
    # "reviewed after receiving it" than "paid" ever was.
    if order_item.order.order_status != Order.OrderStatus.DELIVERED:
        raise ReviewError("You can review a meal once your order has been delivered.")
    if Review.objects.filter(order_item=order_item).exists():
        raise ReviewError("You've already reviewed this meal.")
    if not (1 <= int(rating) <= 5):
        raise ReviewError("Rating must be between 1 and 5.")

    return Review.objects.create(
        order_item=order_item, meal=order_item.meal, rating=rating, comment=comment,
    )
