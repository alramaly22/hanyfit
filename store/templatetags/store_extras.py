"""Template helpers for the store."""

import json

from django import template
from django.utils.safestring import mark_safe

from ..pricing import convert_to_country, currency_label, to_money

register = template.Library()


@register.filter
def money(value, currency=None):
    """Format an amount with a currency: ``1,250 EGP``.

    Pass the currency explicitly wherever it can vary (``|money:store_currency_label``
    or ``|money:order.currency``). With no argument it falls back to the
    store-wide default (Egypt), for the few spots that have no request/order
    to read the real currency from.

    Whole amounts drop the decimals, because "125 EGP" reads better on a
    product card than "125.00 EGP".
    """
    amount = to_money(value)
    label = currency or currency_label()
    if amount == amount.to_integral_value():
        formatted = f"{int(amount):,}"
    else:
        formatted = f"{amount:,.2f}"
    return f"{formatted} {label}"


@register.filter
def convert_currency(value, country_code):
    """Convert a base-currency (EGP) amount into the given country's currency."""
    return convert_to_country(value, country_code)


@register.filter
def plain_money(value):
    """Same formatting without the currency suffix."""
    amount = to_money(value)
    if amount == amount.to_integral_value():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


@register.filter
def json_script_safe(value):
    """Serialise a value for embedding inside a <script> block.

    Escapes the characters that could otherwise close the tag early, so
    attacker-controlled product text cannot break out into executable script.
    """
    dumped = json.dumps(value)
    dumped = (
        dumped.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return mark_safe(dumped)
