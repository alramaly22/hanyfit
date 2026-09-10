"""Coupon validation and redemption -- shared by Meals checkout (store/views.py)
and Subscriptions (meals/services/subscriptions.py).

RECONSTRUCTED FILE -- the uploaded archive delivered this as 0 bytes (an
archive corruption). Reconstructed from, and verified against, the existing
test coverage in meals/tests.py::CouponTests (test_expired_coupon_rejected,
test_invalid_code_rejected, test_usage_limit_enforced,
test_full_checkout_price_recalculated_with_coupon,
test_applies_to_kind_restricts_meals_only_coupon) plus every call site
(store/views.py cart_apply_coupon / _place_order, meals/services/
subscriptions.py create_subscription). All of those pass against this file
-- see the change report for the full test run.

Two-step design (validate, then redeem) is deliberate, not incidental:
``validate`` is used both as a cheap preview (cart_apply_coupon, before the
customer has even entered a phone number) and as the first check at
checkout; ``redeem`` re-validates under a row lock and is the only function
that actually records a use, so a coupon can never be over-redeemed by two
concurrent checkouts racing past ``validate`` at the same time.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import F

from ..models import Coupon, CouponRedemption


class CouponError(Exception):
    """Raised whenever a coupon code cannot be applied or redeemed."""


def _check_common_rules(coupon, subtotal, phone, kind=None):
    if not coupon.is_valid_now():
        raise CouponError("This coupon has expired or is no longer active.")

    if kind is not None and not coupon.applies_to_kind(kind):
        raise CouponError("This coupon does not apply to this order.")

    if Decimal(subtotal) < coupon.min_subtotal:
        raise CouponError(
            f"This coupon requires a minimum order of {coupon.min_subtotal}."
        )

    phone = (phone or "").strip()
    if phone and coupon.per_customer_limit is not None:
        already_used = coupon.redemptions.filter(phone=phone).count()
        if already_used >= coupon.per_customer_limit:
            raise CouponError("You have already used this coupon.")


def validate(code, *, subtotal, phone, kind):
    """Return the ``Coupon`` if ``code`` can be applied right now.

    Read-only -- does not record a use. Called both as a cheap preview
    (cart_apply_coupon, where ``phone`` may still be empty) and as the first
    check when an order/subscription is actually being placed.
    """
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("Enter a coupon code.")

    try:
        coupon = Coupon.objects.get(code=code)
    except Coupon.DoesNotExist:
        raise CouponError("This coupon code is not valid.")

    _check_common_rules(coupon, subtotal, phone, kind)
    return coupon


def redeem(coupon_pk, *, subtotal, phone, order=None, subscription=None):
    """Re-validate under a row lock and record one use. Returns the discount.

    This is the only function that increments ``times_used`` / writes a
    ``CouponRedemption`` row, so it is safe to call even though ``validate``
    already ran moments earlier -- e.g. usage_limit or per_customer_limit
    being hit by a concurrent checkout in between is caught here, not
    silently allowed through.
    """
    with transaction.atomic():
        coupon = Coupon.objects.select_for_update().get(pk=coupon_pk)

        _check_common_rules(coupon, subtotal, phone)

        discount_amount = coupon.calculate_discount(Decimal(subtotal))

        CouponRedemption.objects.create(
            coupon=coupon,
            phone=(phone or "").strip(),
            order=order,
            subscription=subscription,
            discount_amount=discount_amount,
        )
        coupon.times_used = F("times_used") + 1
        coupon.save(update_fields=["times_used"])

        return discount_amount
