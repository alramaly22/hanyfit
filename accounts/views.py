"""Marketing and calculator pages.

The Fawaterk webhook used to live here as well, duplicated with a near
identical copy in store/views.py. Both were stubs that printed the payload and
returned success without verifying a signature or touching the database. There
is now a single implementation in store.views.fawaterk_webhook.
"""

from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def index(request):
    return render(request, "accounts/index.html")


@require_GET
def about(request):
    return render(request, "accounts/about.html")


@require_GET
def pricing(request):
    return render(request, "accounts/pricing.html")


@require_GET
def second(request):
    return render(request, "accounts/second.html")


@require_GET
def book(request):
    return render(request, "accounts/book.html")


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
