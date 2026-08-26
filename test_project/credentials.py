"""Credentials for the HANY APPAREL store, read from environment variables.

=============================================================================
 SECURITY NOTE -- READ BEFORE DEPLOYING
=============================================================================
This file used to hold the real Fawaterk secrets hardcoded in source, at the
owner's request. That has been removed: those values were committed to git
history, so anyone with (past or future) access to the repository could read
them. Two concrete consequences:

  * FAWATERK_HASH_KEY is the secret used to verify payment webhooks. Whoever
    holds it can forge a "paid" callback and mark any order as paid without
    paying.
  * FAWATERK_CLIENT_SECRET can create invoices against the merchant account.

>>> ACTION REQUIRED, even after this change <<<
Because the real values were already committed, rotate/revoke BOTH of them
from the Fawaterk dashboard (Integrations > Fawaterak) and issue new ones,
then set the new values as environment variables below. Simply removing them
from this file does not invalidate a secret an attacker already copied from
git history.

Every value below is read only from the environment now. Nothing here is a
usable fallback -- an unset variable means the corresponding feature (online
payment, tracking, email) is simply disabled until it is configured, the
same way TikTok and email already worked before this change.
"""

import os


def _get(name, default=""):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


# ---------------------------------------------------------------------------
# Fawaterk
# ---------------------------------------------------------------------------
# Dashboard > Integrations > Fawaterak. Set these as environment variables in
# Vercel (Project Settings > Environment Variables) -- never in this file.

FAWATERK_CLIENT_ID = _get("FAWATERK_CLIENT_ID")
FAWATERK_CLIENT_SECRET = _get("FAWATERK_CLIENT_SECRET")
FAWATERK_TOKEN_URL = _get("FAWATERK_TOKEN_URL", "https://app.fawaterk.com/oauth/token")

# Used for two things:
#   1. HMAC-SHA256 verification of incoming webhooks (its main job).
#   2. The static Bearer token sent on API calls.
FAWATERK_HASH_KEY = _get("FAWATERK_HASH_KEY")

# Extra shared secret appended to the refund-webhook URL as a query string
# (?token=...), since Fawaterk's refund callback carries no HMAC hashKey to
# verify. Generate a long random value (e.g. `python -c "import secrets;
# print(secrets.token_urlsafe(32))"`) and set it both here and in the
# webhook URL configured on the Fawaterk dashboard.
FAWATERK_REFUND_WEBHOOK_TOKEN = _get("FAWATERK_REFUND_WEBHOOK_TOKEN")

# Live account. Set FAWATERK_LIVE=0 in the environment to point the API calls
# at staging.fawaterk.com while testing.
FAWATERK_LIVE = _get("FAWATERK_LIVE", "1").strip().lower() in {"1", "true", "yes"}

FAWATERK_API_BASE_URL = _get(
    "FAWATERK_BASE_URL",
    "https://app.fawaterk.com" if FAWATERK_LIVE else "https://staging.fawaterk.com",
).rstrip("/")


# ---------------------------------------------------------------------------
# Order notification email
# ---------------------------------------------------------------------------
# A full order summary is emailed here the moment a payment is verified.
#
# >>> ACTION REQUIRED <<<
# Replace ORDER_NOTIFICATION_EMAIL with the shop owner's real address, and fill
# in the SMTP details below. Until both are set, notification emails are
# printed to the server log instead of being sent, so nothing breaks and no
# order is lost while the mailbox is being set up.

ORDER_NOTIFICATION_EMAIL = _get(
    "ORDER_NOTIFICATION_EMAIL",
    "",  # <-- put the owner's email address here
)

# Optional: a second address (for example the coach's assistant).
ORDER_NOTIFICATION_CC = _get("ORDER_NOTIFICATION_CC", "")

# Send a notification for cash-on-delivery orders too. These are never "paid"
# online, so without this the owner would never hear about them.
NOTIFY_ON_COD_ORDER = _get("NOTIFY_ON_COD_ORDER", "1").strip().lower() in {
    "1", "true", "yes",
}

# --- SMTP -------------------------------------------------------------------
# Gmail: host smtp.gmail.com, port 587, TLS on, and an *app password* rather
# than the account password (Google blocks plain password logins).
# Hostinger: host smtp.hostinger.com, port 465, SSL on.

EMAIL_HOST = _get("EMAIL_HOST", "")
EMAIL_PORT = int(_get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = _get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = _get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _get("EMAIL_USE_TLS", "1").strip().lower() in {"1", "true", "yes"}
EMAIL_USE_SSL = _get("EMAIL_USE_SSL", "0").strip().lower() in {"1", "true", "yes"}

# The From address. Most providers require this to match the authenticated
# mailbox, so it defaults to EMAIL_HOST_USER.
DEFAULT_FROM_EMAIL = _get("DEFAULT_FROM_EMAIL", "") or EMAIL_HOST_USER


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------
# >>> ACTION REQUIRED <<<
# Paste the pixel ID and the Events API access token here. Events Manager >
# your pixel > Settings > "Set up Events API" generates the token.
# While these are empty the pixel simply does not render, which keeps test
# traffic out of the client's ad reporting.

TIKTOK_PIXEL_ID = _get("TIKTOK_PIXEL_ID", "")
TIKTOK_ACCESS_TOKEN = _get("TIKTOK_ACCESS_TOKEN", "")


# ---------------------------------------------------------------------------
# Meta (Facebook/Instagram)
# ---------------------------------------------------------------------------
# >>> ACTION REQUIRED <<<
# Events Manager > Data Sources > your pixel > Settings gives the Pixel ID.
# While this is empty the pixel simply does not render, same as TikTok above.

FACEBOOK_PIXEL_ID = _get("FACEBOOK_PIXEL_ID", "")
