"""Store/Meals money helpers.

Multi-country note
-------------------
Product prices are entered once, in Egypt's currency (EGP) -- the "base"
price. For any other country (see settings.STORE_COUNTRIES), the functions
below convert that base price using a fixed, manually maintained exchange
rate and apply that country's own shipping fee and free-shipping threshold.
There is no live FX lookup.

``ZERO``, ``CENTS`` and ``to_money`` used to be defined here. They are
currency-agnostic (nothing about them is specific to Store or to a
country), so they now live in ``core/pricing.py`` -- the site-wide,
centralized pricing module -- and are re-exported below so every existing
``from .pricing import ZERO, to_money`` elsewhere in this app keeps working
unchanged. This is the only copy of that rounding rule in the project.
"""

from decimal import Decimal

from django.conf import settings

from core.pricing import CENTS, ZERO, to_money  # noqa: F401 -- re-exported

from . import geo


# ---------------------------------------------------------------------------
# Country-aware helpers (used by the cart, checkout and product pages)
# ---------------------------------------------------------------------------

def currency_code_for(country_code):
    """The currency this country is charged in, e.g. 'EGP' or 'SAR'."""
    if country_code == settings.STORE_DEFAULT_COUNTRY:
        return settings.STORE_CURRENCY
    return geo.country_config(country_code)["currency"]


# This store treats the currency code and its display label as the same
# string (EGP, SAR), so the two names are kept as aliases for readability at
# the call site.
currency_label_for = currency_code_for


def exchange_rate_for(country_code):
    return Decimal(str(geo.country_config(country_code)["exchange_rate"]))


def convert_to_country(base_amount, country_code):
    """Convert a base-currency (EGP) amount into the given country's currency."""
    rate = exchange_rate_for(country_code)
    return to_money(to_money(base_amount) * rate)


def shipping_fee_for(country_code):
    if country_code == settings.STORE_DEFAULT_COUNTRY:
        # Egypt reads the original flat settings live (not the STORE_COUNTRIES
        # dict, which is only built once when Django starts) so that both
        # `STORE_SHIPPING_FEE` in the environment and `override_settings(...)`
        # in tests keep working exactly as they did before other countries
        # existed.
        return to_money(settings.STORE_SHIPPING_FEE)
    return to_money(geo.country_config(country_code)["shipping_fee"])


def free_shipping_threshold_for(country_code):
    """Subtotal (in that country's currency) at or above which shipping is free."""
    if country_code == settings.STORE_DEFAULT_COUNTRY:
        return to_money(settings.STORE_FREE_SHIPPING_THRESHOLD)
    return to_money(geo.country_config(country_code)["free_shipping_threshold"])


def shipping_for(subtotal, country_code=None):
    """Shipping cost for a given subtotal, in the given country."""
    country_code = country_code or settings.STORE_DEFAULT_COUNTRY
    subtotal = to_money(subtotal)
    if subtotal <= ZERO:
        return ZERO
    threshold = free_shipping_threshold_for(country_code)
    if threshold > ZERO and subtotal >= threshold:
        return ZERO
    return shipping_fee_for(country_code)


def amount_until_free_shipping(subtotal, country_code=None):
    """How much more the customer needs to spend to unlock free shipping."""
    country_code = country_code or settings.STORE_DEFAULT_COUNTRY
    threshold = free_shipping_threshold_for(country_code)
    if threshold <= ZERO:
        return ZERO
    remaining = threshold - to_money(subtotal)
    return remaining if remaining > ZERO else ZERO


# ---------------------------------------------------------------------------
# Legacy, single-country helpers.
# ---------------------------------------------------------------------------
# Kept for anything not yet made country-aware (background jobs, admin
# displays with no request/session available). Always resolve to Egypt's
# settings-level defaults, same as before this file supported other
# countries.

def shipping_fee():
    return shipping_fee_for(settings.STORE_DEFAULT_COUNTRY)


def free_shipping_threshold():
    return free_shipping_threshold_for(settings.STORE_DEFAULT_COUNTRY)


def currency_code():
    return settings.STORE_CURRENCY


def currency_label():
    return settings.STORE_CURRENCY_LABEL
