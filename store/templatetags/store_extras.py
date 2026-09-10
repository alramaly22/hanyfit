"""Template filters shared by every Store/Meals template.

RECONSTRUCTED FILE -- see the performance/SEO change report for context: the
uploaded archive delivered this file as 0 bytes (an archive corruption, not a
deliberate deletion), so every template that does ``{% load store_extras %}``
would fail with a TemplateSyntaxError until this file exists again.

Only two filters are used anywhere in the project's templates (verified with
a full grep across every .html file):

  * ``money`` -- formats a Decimal/number with a currency label. Its exact
    output is pinned by store/tests.py::test_money_filter_formats_with_the_currency::

        money(Decimal("1250.00"))  == "1,250 EGP"
        money(Decimal("1250.50"))  == "1,250.50 EGP"
        money(None)                == "0 EGP"

    i.e. thousands-separated, trailing ".00" dropped, default currency EGP.

  * ``convert_currency`` -- converts a base EGP amount into a delivery
    country's currency before ``money`` formats it, e.g.
    ``{{ product.price|convert_currency:store_country|money:currency_label }}``.
    This is a thin wrapper around the existing, already-tested
    ``store.pricing.convert_to_country`` -- deliberately not reimplemented
    here, so there is still exactly one exchange-rate/rounding rule in the
    project.

  * ``json_script_safe`` -- used twice, only in checkout.html, to inline the
    TikTok/Facebook pixel "contents" payload straight into a single-quoted
    HTML attribute (``data-tt-properties='...{{ tiktok_contents|json_script_safe }}...'``).
    Django's builtin ``json_script`` filter renders a whole
    ``<script type="application/json">`` tag, which doesn't fit inline like
    this, hence a separate filter. It JSON-encodes the value and escapes the
    handful of characters that would otherwise break out of the surrounding
    attribute or Django's auto-escaping of the ``{{ }}`` (``<``, ``>``, ``&``,
    single quotes) -- the same escaping Django's own ``json_script`` applies
    internally -- then marks the result safe so it isn't HTML-entity-escaped
    a second time on top of that.

No other custom tags or filters are referenced anywhere in the codebase.
"""

import json

from django import template
from django.utils.safestring import mark_safe

from core.pricing import to_money
from store.pricing import convert_to_country

register = template.Library()

# Same escape table Django's own json_script filter uses, so JSON safely
# embedded inside an HTML attribute (single- or double-quoted) can never be
# broken out of or used to inject a </script>-style tag.
_JSON_SCRIPT_ESCAPES = {
    ord(">"): "\\u003e",
    ord("<"): "\\u003c",
    ord("&"): "\\u0026",
    ord("'"): "\\u0027",
}


@register.filter(name="money")
def money(value, currency="EGP"):
    """Format ``value`` as "1,250 EGP" / "1,250.50 EGP" / "0 EGP" for None."""
    amount = to_money(value)
    if amount == amount.to_integral_value():
        formatted = f"{amount:,.0f}"
    else:
        formatted = f"{amount:,.2f}"
    return f"{formatted} {currency}"


@register.filter(name="convert_currency")
def convert_currency(base_amount_egp, country_code):
    """Convert an EGP base price into the given delivery country's currency."""
    return convert_to_country(base_amount_egp, country_code)


@register.filter(name="json_script_safe", is_safe=True)
def json_script_safe(value):
    """JSON-encode ``value`` for safe inline use inside an HTML attribute."""
    return mark_safe(json.dumps(value).translate(_JSON_SCRIPT_ESCAPES))
