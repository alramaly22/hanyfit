"""Marketing and calculator pages.

The Fawaterk webhook used to live here as well, duplicated with a near
identical copy in store/views.py. Both were stubs that printed the payload and
returned success without verifying a signature or touching the database. There
is now a single implementation in store.views.fawaterk_webhook.
"""

from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from . import international_links as intl
from .geo import is_egypt


@require_GET
@never_cache
def index(request):
    return render(
        request,
        "accounts/index.html",
        {
            "is_egypt": is_egypt(request),
            "coaching_link_1": intl.coaching_link("month_1"),
            "coaching_link_2": intl.coaching_link("month_2"),
            "coaching_link_3": intl.coaching_link("month_3"),
        },
    )


@require_GET
def about(request):
    return render(request, "accounts/about.html")


@require_GET
@never_cache
def pricing(request):
    return render(
        request,
        "accounts/pricing.html",
        {
            "is_egypt": is_egypt(request),
            "coaching_link_1": intl.coaching_link("month_1"),
            "coaching_link_2": intl.coaching_link("month_2"),
            "coaching_link_3": intl.coaching_link("month_3"),
        },
    )


@require_GET
def second(request):
    return render(request, "accounts/second.html")


@require_GET
@never_cache
def book(request):
    return render(
        request,
        "accounts/book.html",
        {
            "is_egypt": is_egypt(request),
            "book_link_recipe_ar": intl.book_link("recipe_mastery", "ar"),
            "book_link_recipe_en": intl.book_link("recipe_mastery", "en"),
            "book_link_supplements_ar": intl.book_link("supplements_guide", "ar"),
            "book_link_supplements_en": intl.book_link("supplements_guide", "en"),
            "book_link_exercise_ar": intl.book_link("exercise_guide", "ar"),
            "book_link_exercise_en": intl.book_link("exercise_guide", "en"),
            "book_link_substances_ar": intl.book_link("substances_risks", "ar"),
            "book_link_substances_en": intl.book_link("substances_risks", "en"),
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