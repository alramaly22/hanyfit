"""robots.txt and sitemap.xml.

RECONSTRUCTED FILE -- the uploaded archive delivered this as 0 bytes (an
archive corruption). test_project/urls.py imports ``accounts_seo.robots_txt``
and ``accounts_seo.sitemap_xml`` unconditionally, so with the file empty the
whole URLconf failed to load and *every* page on the site 500'd -- this was
the single highest-priority fix, ahead of any optimization work.

No new dependency (e.g. django.contrib.sitemaps) is introduced: both views
are plain, cacheable HttpResponses, which keeps things simple for a site
this size and avoids adding an app that would need registering.

Cache note: both are wrapped in Django's page cache for a day. Neither
depends on request/session state, both are hit by crawlers repeatedly, and
each currently costs at least one DB query (sitemap) -- caching keeps that
off the hot path without adding a dependency (uses the existing DatabaseCache
already configured in settings.CACHES).
"""

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
# Everything customer-account-specific or already marked
# `{% block robots %}noindex, nofollow{% endblock %}` in its template (cart,
# checkout, order pages, subscription management) is also kept out of the
# crawl here, so crawlers don't spend budget on pages that were never going
# to be indexed anyway and no order/subscription token ever shows up in
# Search Console.

_ROBOTS_TEMPLATE = """User-agent: *
Allow: /

Disallow: /admin/
Disallow: /dashboard/
Disallow: /store/cart/
Disallow: /store/checkout/
Disallow: /store/order/
Disallow: /store/payment/
Disallow: /meals/subscribe/
Disallow: /meals/subscription/
Disallow: /meals/reviews/

Sitemap: {sitemap_url}
"""


@require_GET
@cache_page(60 * 60 * 24)
def robots_txt(request):
    sitemap_url = f"{settings.SITE_URL}{reverse('sitemap_xml')}"
    return HttpResponse(
        _ROBOTS_TEMPLATE.format(sitemap_url=sitemap_url),
        content_type="text/plain",
    )


# ---------------------------------------------------------------------------
# sitemap.xml
# ---------------------------------------------------------------------------

# (url name, changefreq, priority) for every page that is meant to be
# indexed. Anything with its own `{% block robots %}noindex, nofollow{% endblock %}`
# (cart, checkout, order/*, dashboard/*) is deliberately left out -- listing
# a page here while it tells crawlers not to index it sends Google a mixed
# signal for no benefit.
_STATIC_PAGES = [
    ("index", "weekly", "1.0"),
    ("about", "monthly", "0.6"),
    ("pricing", "monthly", "0.7"),
    ("book", "monthly", "0.6"),
    ("protein", "monthly", "0.5"),
    ("calories", "monthly", "0.5"),
    ("proteinen", "monthly", "0.5"),
    ("caloriesen", "monthly", "0.5"),
    ("store", "daily", "0.8"),
    ("meals:home", "daily", "0.9"),
    ("meals:meal_list", "daily", "0.8"),
    ("meals:calculator", "monthly", "0.6"),
]


def _url_entry(loc, lastmod=None, changefreq="weekly", priority="0.5"):
    entry = [
        "  <url>",
        f"    <loc>{loc}</loc>",
    ]
    if lastmod is not None:
        entry.append(f"    <lastmod>{lastmod.date().isoformat()}</lastmod>")
    entry.append(f"    <changefreq>{changefreq}</changefreq>")
    entry.append(f"    <priority>{priority}</priority>")
    entry.append("  </url>")
    return "\n".join(entry)


@require_GET
@cache_page(60 * 60 * 24)
def sitemap_xml(request):
    # Imported here rather than at module level: accounts is loaded before
    # store/meals in INSTALLED_APPS (see settings.py), and importing their
    # models at import time would risk a circular/registry-not-ready import
    # depending on load order. By the time a request comes in the app
    # registry is always fully populated, so this is both safe and cheap.
    from meals.models import Meal
    from store.models import Product

    site_url = settings.SITE_URL.rstrip("/")
    now = timezone.now()

    entries = [
        _url_entry(f"{site_url}{reverse(name)}", now, changefreq, priority)
        for name, changefreq, priority in _STATIC_PAGES
    ]

    for product in Product.objects.filter(is_active=True).only(
        "slug", "updated_at"
    ):
        entries.append(
            _url_entry(
                f"{site_url}{product.get_absolute_url()}",
                product.updated_at,
                "weekly",
                "0.7",
            )
        )

    for meal in Meal.objects.filter(is_active=True).only("slug", "updated_at"):
        entries.append(
            _url_entry(
                f"{site_url}{meal.get_absolute_url()}",
                meal.updated_at,
                "weekly",
                "0.7",
            )
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>"
    )
    return HttpResponse(xml, content_type="application/xml")
