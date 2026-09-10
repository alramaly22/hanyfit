"""Product + currency -> payment link registry (Online Coaching, Books).

Single source of truth for every coaching-package and book payment link.
Before this file existed, the EGP links lived hardcoded inline in the
accounts templates (pricing.html / index.html / book.html) and the SAR
links lived in accounts/international_links.py, with a template-level
``{% if is_egypt %}`` choosing between the two. Every value below is
copied unchanged from one of those two places -- nothing here is a new or
invented link.

Online Coaching and Books have no Order/Payment model of their own yet
(see the country/currency audit): a "payment link" is a hosted Fawaterk
checkout page created manually in the Fawaterk dashboard, or a WhatsApp
fallback while a link for a given (product, currency) combination has not
been created yet. Nothing here charges anything itself.

KNOWN GAPS -- see missing_combinations() below. Every SAR entry was already
blank before this refactor (Fawaterk links for the Saudi packages/books had
not been created yet); they are carried over as blank rather than invented,
so the WhatsApp fallback keeps covering them exactly as it did before.
"""

from django.conf import settings

# ---------------------------------------------------------------------------
# Online Coaching -- 1 / 2 / 3 month packages.
# EGP links copied from accounts/templates/accounts/pricing.html (and the
# identical ones in index.html). SAR links copied from the previous
# accounts/international_links.py:COACHING_SAR_LINKS.
# ---------------------------------------------------------------------------
COACHING_LINKS = {
    "month_1": {  # 1 Month -- 599 EGP / 199 SAR
        "EGP": "https://app.fawaterk.com/pg/ashtrak-shhr1",
        "SAR": "",
    },
    "month_2": {  # 2 Months -- 1,099 EGP / 299 SAR
        "EGP": "https://app.fawaterk.com/pg/ashtrk-shhryn-2",
        "SAR": "",
    },
    "month_3": {  # 3 Months -- 1,499 EGP / 399 SAR
        "EGP": "https://app.fawaterk.com/paymentRequest/show/40531",
        "SAR": "",
    },
}

# ---------------------------------------------------------------------------
# Books -- each has one Arabic link + one English link per currency.
# EGP links copied from accounts/templates/accounts/book.html. SAR links
# copied from the previous accounts/international_links.py:BOOKS_SAR_LINKS.
# ---------------------------------------------------------------------------
BOOK_LINKS = {
    "recipe_mastery": {  # Recipe Mastery Book -- 149 EGP / 50 SAR
        "EGP": {
            "ar": "https://app.fawaterk.com/pg/ktab-osfat",
            "en": "https://app.fawaterk.com/pg/healthy-recipes-book",
        },
        "SAR": {"ar": "", "en": ""},
    },
    "supplements_guide": {  # Ultimate Supplements Guide -- 149 EGP / 50 SAR
        "EGP": {
            "ar": "https://app.fawaterk.com/pg/ktab-mkmlat",
            "en": "https://app.fawaterk.com/pg/supplements-1",
        },
        "SAR": {"ar": "", "en": ""},
    },
    "exercise_guide": {  # Exercise Training Guide -- 149 EGP / 50 SAR
        "EGP": {
            "ar": "https://app.fawaterk.com/pg/ktab-tmryn0",
            "en": "https://app.fawaterk.com/pg/workout-guide",
        },
        "SAR": {"ar": "", "en": ""},
    },
    "substances_risks": {  # Substances: Risks & Consequences -- 149 EGP / 50 SAR
        "EGP": {
            "ar": "https://app.fawaterk.com/pg/ktab-almoad-almhsn1",
            "en": "https://app.fawaterk.com/pg/performance-enhancing-substances-book1",
        },
        "SAR": {"ar": "", "en": ""},
    },
}


def _whatsapp_fallback():
    """Shown instead of a blank link, so a button never dead-ends.

    Built from settings.STORE_WHATSAPP_NUMBER rather than a second
    hardcoded phone number, so there is one place that number is
    maintained.
    """
    return f"https://wa.me/{settings.STORE_WHATSAPP_NUMBER}"


def coaching_link(product_key, currency_code):
    """The payment link for a coaching package, in the given currency."""
    return (COACHING_LINKS.get(product_key) or {}).get(currency_code) or _whatsapp_fallback()


def book_link(product_key, currency_code, lang):
    """The payment link for a book, in the given currency and language."""
    by_currency = BOOK_LINKS.get(product_key) or {}
    return (by_currency.get(currency_code) or {}).get(lang) or _whatsapp_fallback()


def missing_combinations():
    """(kind, product_key, currency_code[, lang]) tuples with no link yet.

    Nothing here is fixed automatically -- this only reports the gap so it
    can be filled with a real link before it matters. See core/tests.py,
    which surfaces the current list without failing the build over it.
    """
    missing = []
    for key, by_currency in COACHING_LINKS.items():
        for currency_code, link in by_currency.items():
            if not link:
                missing.append(("coaching", key, currency_code))
    for key, by_currency in BOOK_LINKS.items():
        for currency_code, by_lang in by_currency.items():
            for lang, link in by_lang.items():
                if not link:
                    missing.append(("book", key, currency_code, lang))
    return missing
