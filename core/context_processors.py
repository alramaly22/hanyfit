"""Exposes the resolved country/currency context to every template.

Registered once in TEMPLATES.OPTIONS.context_processors (see settings.py),
so any template anywhere -- accounts, store, meals -- can read
``selected_currency`` without its view having to compute it by hand.
"""

from . import currency, geo


def currency_context(request):
    detected_country = geo.detect_country(request)
    return {
        "detected_country": detected_country,
        "default_currency": currency.get_default_currency(detected_country),
        "selected_currency": currency.resolve_currency(request),
        "supported_currencies": currency.supported_currencies(),
    }
