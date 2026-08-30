"""International (non-Egypt) pricing links -- Saudi Riyal (SAR).

Egypt keeps its existing EGP prices and Fawaterk links untouched, directly
in templates/accounts/pricing.html and templates/accounts/book.html -- this
file only covers the "visitor is outside Egypt" case (see accounts/geo.py).

HOW TO USE
----------
Once you create a Fawaterk payment-request link for each item below, paste
it as the string value. Leave it as "" until then -- the page falls back to
a WhatsApp contact link automatically, so an international visitor is never
sent to a dead link while you're still setting these up.

Prices themselves are already wired into the templates (199 / 299 / 399 SAR
for coaching, 50 SAR flat for every book) -- you only need to paste links
here, not prices. If a price ever needs to change, tell me and I'll update
it in the templates directly.
"""

# ---------------------------------------------------------------------------
# Online coaching -- 199 / 299 / 399 SAR for 1 / 2 / 3 months.
# ---------------------------------------------------------------------------
COACHING_SAR_LINKS = {
    "month_1": "",   # 1 Month Online Coaching -- 199 SAR
    "month_2": "",   # 2 Months Online Coaching -- 299 SAR
    "month_3": "",   # 3 Months Online Coaching -- 399 SAR
}

# ---------------------------------------------------------------------------
# Books -- 50 SAR flat, each book needs one Arabic link + one English link
# (same language-choice modal as the Egypt version).
# ---------------------------------------------------------------------------
BOOKS_SAR_LINKS = {
    "recipe_mastery": {"ar": "", "en": ""},     # Recipe Mastery Book
    "supplements_guide": {"ar": "", "en": ""},  # Ultimate Supplements Guide
    "exercise_guide": {"ar": "", "en": ""},     # Exercise Training Guide
    "substances_risks": {"ar": "", "en": ""},   # Substances: Risks & Consequences
}

# Shown instead of an empty link above, so a button never dead-ends while a
# link is still missing.
FALLBACK_WHATSAPP_LINK = "https://wa.me/966578079833"


def coaching_link(key):
    return COACHING_SAR_LINKS.get(key) or FALLBACK_WHATSAPP_LINK


def book_link(key, lang):
    return (BOOKS_SAR_LINKS.get(key) or {}).get(lang) or FALLBACK_WHATSAPP_LINK