"""Centralized currency resolution -- the single source of truth for which
currency a visitor sees and pays in, anywhere on the site.

    detect_country(request)                    (core.geo)
            |
            v
    get_default_currency(country_code)
            |
            v
    get_currency_override(request)  -- a valid, current-version manual choice
            |
            v
    resolve_currency(request)                    <-- everything else calls THIS

Country and currency are deliberately different things here: detecting a
visitor as Egyptian only ever picks a *default*. A manually selected
currency (stored server-side, in the session) always wins over the
default, and choosing a different currency never changes the detected
country. Store/Meals keep their own, separate delivery-country switcher
(store/geo.py) for shipping purposes -- see store/views.set_country, which
calls set_currency_override() below to keep the two in sync when a
customer explicitly changes delivery country, without making the reverse
true (changing currency on a Coaching/Books page never touches delivery
country).
"""

from django.conf import settings

from . import geo

SESSION_CURRENCY_KEY = "selected_currency"
SESSION_CURRENCY_VERSION_KEY = "currency_context_version"

# Any country not listed in settings.STORE_COUNTRIES (Store currently only
# ships to EG/SA) falls back to this currency as its default -- matching
# the original "Egypt -> EGP, everywhere else -> SAR" rule.
DEFAULT_CURRENCY_FOR_UNLISTED_COUNTRY = "SAR"


def supported_currencies():
    """The whitelist of currencies a visitor is ever allowed to select."""
    return list(settings.SUPPORTED_CURRENCIES)


def is_supported_currency(code):
    return bool(code) and code in settings.SUPPORTED_CURRENCIES


def get_default_currency(country_code):
    """The currency a visitor from this country sees before any override.

    Reuses settings.STORE_COUNTRIES as the base source of truth -- it
    already carries a vetted currency-per-country mapping for Store/Meals,
    so this is not a second, independently maintained country->currency
    table.
    """
    country_config = settings.STORE_COUNTRIES.get(country_code)
    if country_config:
        return country_config["currency"]
    return DEFAULT_CURRENCY_FOR_UNLISTED_COUNTRY


def get_currency_override(request):
    """The visitor's manually-chosen currency, or None if there isn't one.

    Anything stored under an older (or missing) CURRENCY_CONTEXT_VERSION --
    every session that existed before this system was introduced, or before
    a future change to how currency is resolved -- is treated as if it were
    never set. It is not migrated or read, just ignored: this is what
    guarantees a legacy session cannot keep showing a stale currency after
    a deployment changes this logic, with no dependency on the visitor
    clearing anything themselves. The old value is left untouched in the
    session (harmless, and cheaper than rewriting it); it simply stops
    being read.
    """
    session = getattr(request, "session", None)
    if session is None:
        return None
    if session.get(SESSION_CURRENCY_VERSION_KEY) != settings.CURRENCY_CONTEXT_VERSION:
        return None
    value = session.get(SESSION_CURRENCY_KEY)
    return value if is_supported_currency(value) else None


def set_currency_override(request, currency_code):
    """Persist a manually-chosen currency for this session.

    Returns False (and stores nothing) for anything outside the whitelist --
    this is the only path that can ever change the stored currency, and it
    never trusts a client-supplied value without checking it first.
    """
    session = getattr(request, "session", None)
    if session is None or not is_supported_currency(currency_code):
        return False
    session[SESSION_CURRENCY_KEY] = currency_code
    session[SESSION_CURRENCY_VERSION_KEY] = settings.CURRENCY_CONTEXT_VERSION
    return True


def resolve_currency(request):
    """The one function every app calls to know which currency to show/charge.

    A valid, current manual override always wins; otherwise the default for
    the detected country.
    """
    override = get_currency_override(request)
    if override:
        return override
    country = geo.detect_country(request)
    return get_default_currency(country)
