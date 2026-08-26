"""SEO: sitemap.xml and robots.txt for the whole site.

Deliberately not using django.contrib.sitemaps/django.contrib.sites -- that
framework wants SITE_ID and a Sites row kept in sync with the real domain,
which is one more thing to misconfigure across environments (local, Vercel
preview, production custom domain). This project already solved "what's our
real domain" once, in settings.SITE_URL (env-driven, falls back to the
Vercel host, then localhost) -- these two views just reuse that.

Covers all three parts of the business (coaching pages, apparel store, meal
subscriptions/catalogue) so none of them is missing from what search engines
are told about, and lists individual products/meals so their pages -- not
just the section homepages -- are discoverable.
"""

from django.core.cache import cache
from django.http import HttpResponse
from django.urls import reverse

from meals.models import Meal
from store.models import Product

STATIC_URLS = [
    ("index", 1.0, "daily"),
    ("about", 0.6, "monthly"),
    ("pricing", 0.6, "monthly"),
    ("book", 0.6, "monthly"),
    ("calories", 0.5, "monthly"),
    ("caloriesen", 0.5, "monthly"),
    ("protein", 0.5, "monthly"),
    ("proteinen", 0.5, "monthly"),
    ("store", 0.9, "daily"),
    ("meals:home", 1.0, "daily"),
    ("meals:meal_list", 0.9, "daily"),
    ("meals:calculator", 0.7, "weekly"),
]


def _entries():
    entries = []

    for name, priority, changefreq in STATIC_URLS:
        entries.append({"loc": reverse(name), "priority": priority, "changefreq": changefreq})

    for product in Product.objects.filter(is_active=True).only("slug"):
        entries.append({
            "loc": product.get_absolute_url(),
            "priority": 0.8,
            "changefreq": "weekly",
        })

    for meal in Meal.objects.filter(is_active=True).only("slug", "updated_at"):
        entries.append({
            "loc": meal.get_absolute_url(),
            "priority": 0.8,
            "changefreq": "weekly",
            "lastmod": meal.updated_at,
        })

    return entries


def sitemap_xml(request):
    """Cached for an hour -- the catalogue doesn't change often enough to
    justify rebuilding this on every crawler hit."""
    xml = cache.get("sitemap_xml")
    if xml is None:
        from django.conf import settings

        base = settings.SITE_URL
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for entry in _entries():
            lines.append("  <url>")
            lines.append(f"    <loc>{base}{entry['loc']}</loc>")
            if entry.get("lastmod"):
                lines.append(f"    <lastmod>{entry['lastmod'].date().isoformat()}</lastmod>")
            lines.append(f"    <changefreq>{entry['changefreq']}</changefreq>")
            lines.append(f"    <priority>{entry['priority']}</priority>")
            lines.append("  </url>")
        lines.append("</urlset>")
        xml = "\n".join(lines)
        cache.set("sitemap_xml", xml, 60 * 60)

    return HttpResponse(xml, content_type="application/xml")


def robots_txt(request):
    from django.conf import settings

    lines = [
        "User-agent: *",
        "Disallow: /dashboard/",
        "Disallow: /admin/",
        "Disallow: /store/cart/",
        "Disallow: /store/checkout/",
        "Disallow: /meals/subscribe/",
        "Disallow: /meals/subscription/",
        "Disallow: /store/orders/",
        "Disallow: /meals/reviews/",
        "",
        f"Sitemap: {settings.SITE_URL}{reverse('sitemap_xml')}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
