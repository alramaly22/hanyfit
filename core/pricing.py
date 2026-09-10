"""Money helpers shared across the whole site.

``ZERO``, ``CENTS`` and ``to_money`` used to live in ``store/pricing.py``
only. They are pure, currency-agnostic helpers (nothing here is aware of
countries, shipping, or Fawaterk), so they belong here instead --
``store/pricing.py`` now imports them from this module rather than
defining its own copy, so there is exactly one rounding rule in the
project. All money is handled as Decimal and rounded to two places at the
boundary; floats are deliberately avoided.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def to_money(value):
    """Coerce anything numeric into a 2dp Decimal."""
    if value in (None, ""):
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Currency-based conversion (Online Coaching, Books -- anything priced by
# *currency* rather than by *delivery country*).
# ---------------------------------------------------------------------------
# Store/Meals convert by country (store/pricing.py: convert_to_country),
# because a delivery country also carries a shipping fee and a governorate
# list -- that conversion is left untouched there. This one is for the
# opposite case: a currency chosen independently of any country, for
# products that are never shipped. Both ultimately read the same exchange
# rate out of settings.STORE_COUNTRIES, so the rate itself is not
# maintained twice.

def exchange_rate_for_currency(currency_code):
    """The manually maintained EGP -> currency_code rate.

    Looked up from settings.STORE_COUNTRIES (the same table Store/Meals
    already use), so this is not a second, independently maintained rate
    table -- just a lookup by currency instead of by country.
    """
    if currency_code == settings.STORE_CURRENCY:
        return Decimal("1")
    for country_config in settings.STORE_COUNTRIES.values():
        if country_config["currency"] == currency_code:
            return Decimal(str(country_config["exchange_rate"]))
    raise ValueError(f"No exchange rate configured for currency {currency_code!r}")


def convert_price(base_amount_egp, currency_code):
    """Convert an EGP base price into the given currency."""
    rate = exchange_rate_for_currency(currency_code)
    return to_money(to_money(base_amount_egp) * rate)
