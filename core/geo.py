"""Site-wide visitor country detection.

Single source of truth for "which country is this request coming from",
used by core.currency to pick a default currency. Nothing here decides a
currency or a price directly, and nothing here is persisted -- see
core/currency.py for why country and currency are kept as separate
concerns, and store/geo.py for the *separate*, session-based delivery
country a customer explicitly picks in Store/Meals (that one is untouched
by this module; see this project's country/currency audit for why the two
are not merged).
"""

from django.conf import settings


def detect_country(request):
    """Best-guess visitor country, recomputed fresh on every request.

    Vercel's edge network stamps every request with an
    ``x-vercel-ip-country`` header (ISO 3166-1 alpha-2, e.g. "EG", "SA")
    before it ever reaches Django -- no external geolocation API call and no
    GeoIP database needed.

    Locally, on any host other than Vercel, or for the rare visitor Vercel
    could not geolocate at all, that header is simply absent. This falls
    back to ``settings.STORE_DEFAULT_COUNTRY`` (Egypt) in that case -- the
    safe choice for local development (no surprise foreign pricing while
    testing) and the closest thing this project has to a "home market".

    This value is never stored anywhere: a detection failure or a wrong
    guess only ever affects the *default* a visitor starts with, never
    something they are stuck with, because core.currency.resolve_currency
    always lets a manual currency choice override it.
    """
    country = request.META.get("HTTP_X_VERCEL_IP_COUNTRY", "")
    country = country.strip().upper()
    return country or settings.STORE_DEFAULT_COUNTRY
