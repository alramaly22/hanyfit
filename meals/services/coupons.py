"""Coupon validation + redemption.

The discount amount is always (re)computed here from the server-side
subtotal -- a coupon code from the frontend is just a lookup key, never a
carrier of the discount amount itself.
"""

from decimal import Decimal

from django.db import transaction

from ..models import Coupon, CouponRedemption


class CouponError(Exception):
    """Raised with a user-facing reason a coupon could not be applied."""


def validate(code, *, subtotal, phone, kind):
    """Return a valid, applicable Coupon for this subtotal/phone/kind, or raise CouponError."""
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("Enter a coupon code.")

    try:
        coupon = Coupon.objects.get(code=code)
    except Coupon.DoesNotExist:
        raise CouponError("This coupon code does not exist.")

    if not coupon.is_valid_now():
        raise CouponError("This coupon is no longer valid.")

    if not coupon.applies_to_kind(kind):
        raise CouponError("This coupon does not apply to this type of order.")

    if Decimal(subtotal) < coupon.min_subtotal:
        raise CouponError(f"This coupon requires a minimum order of {coupon.min_subtotal} EGP.")

    if coupon.per_customer_limit is not None and phone:
        used = CouponRedemption.objects.filter(coupon=coupon, phone=phone).count()
        if used >= coupon.per_customer_limit:
            raise CouponError("You have already used this coupon the maximum number of times.")

    return coupon


@transaction.atomic
def redeem(coupon_id, *, subtotal, phone, order=None, subscription=None):
    """Atomically re-validate, lock the usage counters, and record a redemption.

    Called at the moment an order/subscription is actually created (not when
    the coupon is merely previewed), so a race between two concurrent
    checkouts against the same near-exhausted coupon cannot double-spend it.
    Returns the discount amount actually applied.
    """
    coupon = Coupon.objects.select_for_update().get(pk=coupon_id)

    if not coupon.is_valid_now():
        raise CouponError("This coupon is no longer valid.")
    if coupon.usage_limit is not None and coupon.times_used >= coupon.usage_limit:
        raise CouponError("This coupon has reached its usage limit.")
    if coupon.per_customer_limit is not None and phone:
        used = CouponRedemption.objects.filter(coupon=coupon, phone=phone).count()
        if used >= coupon.per_customer_limit:
            raise CouponError("You have already used this coupon the maximum number of times.")

    discount_amount = coupon.calculate_discount(subtotal)

    coupon.times_used = coupon.times_used + 1
    coupon.save(update_fields=["times_used"])

    CouponRedemption.objects.create(
        coupon=coupon,
        phone=phone,
        order=order,
        subscription=subscription,
        discount_amount=discount_amount,
    )

    return discount_amount
