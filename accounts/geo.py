"""Automatic country detection for the marketing pages (accounts app).

This is deliberately separate from store/geo.py, which is a manual,
session-based country *switcher* used by the shop's checkout flow (see that
module's docstring) -- that one is untouched by this file and keeps working
exactly as before.

This module answers a narrower question, automatically and with no visitor
interaction: is this request coming from Egypt? It is used only by the
online-coaching pricing page and the books page, to decide EGP-vs-SAR
pricing and which Fawaterk links to show.
"""


def is_egypt(request):
    """True unless the visitor is geolocated outside Egypt.

    Vercel's edge network stamps every request with an
    ``x-vercel-ip-country`` header (ISO 3166-1 alpha-2, e.g. "EG", "SA")
    before it ever reaches Django -- no external geolocation API call and no
    GeoIP database needed.

    Locally, or on any host other than Vercel, that header is simply absent.
    This defaults to Egypt in that case -- the safe choice for local
    development (no surprise SAR pricing while testing) and for the rare
    visitor Vercel could not geolocate at all.
    """
    country = request.META.get("HTTP_X_VERCEL_IP_COUNTRY", "")
    if not country:
        return True
    return country.strip().upper() == "EG"