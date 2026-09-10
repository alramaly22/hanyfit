"""Marketing and calculator pages.

The Fawaterk webhook used to live here as well, duplicated with a near
identical copy in store/views.py. Both were stubs that printed the payload and
returned success without verifying a signature or touching the database. There
is now a single implementation in store.views.fawaterk_webhook.

Country/currency note
----------------------
Coaching-package and book pricing/links used to be decided by
accounts.geo.is_egypt(request), a boolean computed only from the Vercel IP
header, entirely separate from Store/Meals' own session-based country
switcher. Both are now replaced by core.currency.resolve_currency(request)
-- the one, site-wide currency resolution (auto-detected country, with a
manual override that always wins) shared by every app. accounts/geo.py and
accounts/international_links.py have been folded into core/geo.py and
core/payment_links.py respectively.
"""

from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core import payment_links
from core.currency import resolve_currency


@require_GET
@never_cache
def index(request):
    currency = resolve_currency(request)
    return render(
        request,
        "accounts/index.html",
        {
            "selected_currency": currency,
            "coaching_link_1": payment_links.coaching_link("month_1", currency),
            "coaching_link_2": payment_links.coaching_link("month_2", currency),
            "coaching_link_3": payment_links.coaching_link("month_3", currency),
            "protein_calc_link": payment_links.calculator_link("protein", currency),
            "calories_calc_link": payment_links.calculator_link("calories", currency),
        },
    )


@require_GET
def about(request):
    return render(request, "accounts/about.html")


@require_GET
@never_cache
def pricing(request):
    currency = resolve_currency(request)
    return render(
        request,
        "accounts/pricing.html",
        {
            "selected_currency": currency,
            "coaching_link_1": payment_links.coaching_link("month_1", currency),
            "coaching_link_2": payment_links.coaching_link("month_2", currency),
            "coaching_link_3": payment_links.coaching_link("month_3", currency),
        },
    )


@require_GET
def second(request):
    return render(request, "accounts/second.html")


@require_GET
@never_cache
def book(request):
    currency = resolve_currency(request)
    return render(
        request,
        "accounts/book.html",
        {
            "selected_currency": currency,
            "book_link_recipe_ar": payment_links.book_link("recipe_mastery", currency, "ar"),
            "book_link_recipe_en": payment_links.book_link("recipe_mastery", currency, "en"),
            "book_link_supplements_ar": payment_links.book_link("supplements_guide", currency, "ar"),
            "book_link_supplements_en": payment_links.book_link("supplements_guide", currency, "en"),
            "book_link_exercise_ar": payment_links.book_link("exercise_guide", currency, "ar"),
            "book_link_exercise_en": payment_links.book_link("exercise_guide", currency, "en"),
            "book_link_substances_ar": payment_links.book_link("substances_risks", currency, "ar"),
            "book_link_substances_en": payment_links.book_link("substances_risks", currency, "en"),
        },
    )


# --- Calculators ---------------------------------------------------------

@require_GET
def protein(request):
    return render(request, "accounts/protein.html")


@require_GET
def calories(request):
    return render(request, "accounts/calories.html")


@require_GET
def proteinen(request):
    return render(request, "accounts/proteinen.html")


@require_GET
def caloriesen(request):
    """English calorie calculator.

    The template existed in the repo but had no view or URL, so the page was
    unreachable and the English protein page linked to a 404.
    """
    return render(request, "accounts/caloriesen.html")