"""Delivery-country selection.

The store now ships to more than one country (see settings.STORE_COUNTRIES).
Which one a visitor is browsing in decides the currency shown, the shipping
fee, and whether the checkout page shows a governorate dropdown. It is kept
in the session -- the same way the cart is -- so it survives navigation
without needing a query string on every link.
"""

from django.conf import settings

SESSION_KEY = "store_country"


def available_countries():
    """(code, config) pairs, in the order declared in settings, for a switcher."""
    return list(settings.STORE_COUNTRIES.items())


def is_valid_country(code):
    return bool(code) and code in settings.STORE_COUNTRIES


def get_country(request):
    """The country code the current visitor is browsing/checking out in."""
    session = getattr(request, "session", None)
    code = session.get(SESSION_KEY) if session is not None else None
    if is_valid_country(code):
        return code
    return settings.STORE_DEFAULT_COUNTRY


def set_country(request, code):
    """Persist a valid country choice to the session. Returns False if invalid."""
    if not is_valid_country(code):
        return False
    request.session[SESSION_KEY] = code
    return True


def country_config(request_or_code):
    """Accepts either a request (resolves via the session) or a country code."""
    code = (
        request_or_code
        if isinstance(request_or_code, str)
        else get_country(request_or_code)
    )
    return settings.STORE_COUNTRIES.get(
        code, settings.STORE_COUNTRIES[settings.STORE_DEFAULT_COUNTRY]
    )


def has_regions(country_code):
    """True when the country uses a structured governorate/region list."""
    return bool(country_config(country_code).get("has_regions"))
