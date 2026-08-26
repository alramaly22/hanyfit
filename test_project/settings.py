"""
Django settings for test_project.

Configuration is driven by environment variables so the same code can run
locally (SQLite, DEBUG on) and on Vercel (Neon Postgres, DEBUG off) without
edits. See .env.example for the full list of supported variables.
"""

import os
from decimal import Decimal
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from . import credentials

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def env_bool(name, default=False):
    """Read a boolean from the environment ('1', 'true', 'yes' are truthy)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=None):
    """Read a comma-separated list from the environment."""
    raw = os.environ.get(name, "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or (default or [])


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", default=True)

# In DEBUG we fall back to a throwaway key so a fresh clone runs with no setup.
# In production a missing key is a hard error rather than a silent weak
# default -- the fallback below is committed to git history, so using it in
# production would mean every session/CSRF/signed-cookie on the live site is
# forgeable by anyone who has ever seen this repository.
_INSECURE_DEV_SECRET_KEY = "django-insecure-vercel-test-key-change-before-production"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")

if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = _INSECURE_DEV_SECRET_KEY
    else:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is not set. Generate one (e.g. `python -c "
            "\"import secrets; print(secrets.token_urlsafe(64))\"`) and set it "
            "as an environment variable before deploying with DEBUG off."
        )


ALLOWED_HOSTS = ["*"]

_vercel_host = os.environ.get("VERCEL_URL")

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

if not SITE_URL and _vercel_host:
    SITE_URL = f"https://{_vercel_host}"

if not SITE_URL:
    SITE_URL = "http://127.0.0.1:8000"

CSRF_TRUSTED_ORIGINS = [
    "https://*.vercel.app",
]

if _vercel_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_vercel_host}")

# The real, custom domain (Hostinger DNS -> Vercel) is not a *.vercel.app
# address, so it needs to be trusted explicitly -- otherwise every POST
# (checkout, dashboard actions, admin login, meal subscriptions) fails with
# a CSRF error the moment traffic comes in through that domain instead of
# the vercel.app one. Derived from SITE_URL automatically; add any extra
# domains (e.g. with and without "www") via the CSRF_TRUSTED_ORIGINS env var,
# comma-separated, full https:// origins.
if SITE_URL and SITE_URL not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(SITE_URL)

CSRF_TRUSTED_ORIGINS.extend(env_list("CSRF_TRUSTED_ORIGINS"))
# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "store",
    "meals",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Logs one row per page view, for the "visitors" numbers on /dashboard/.
    # Must stay after SessionMiddleware (needs request.session) and is
    # best-effort: it never raises, so a tracking hiccup cannot break a page.
    "store.middleware.VisitTrackingMiddleware",
]

ROOT_URLCONF = "test_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # accounts/templates is found by APP_DIRS, which is also how
        # partials/tracking.html resolves.
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Exposes cart_count / cart_total and the tracking IDs to every
                # template, so the header badge and the pixel work site-wide.
                "store.context_processors.cart",
                "store.context_processors.tracking",
            ],
        },
    },
]

WSGI_APPLICATION = "test_project.wsgi.application"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Set DATABASE_URL to the Neon connection string in production, e.g.
#   postgresql://user:pass@ep-xxx.eu-central-1.aws.neon.tech/dbname?sslmode=require
# Without it we fall back to local SQLite.

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            # Serverless functions are short-lived, so persistent connections
            # only tie up Neon's connection slots. Use Neon's pooled endpoint.
            conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "0")),
            conn_health_checks=False,
            ssl_require=env_bool("DB_SSL_REQUIRE", default=True),
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Africa/Cairo")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "accounts" / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

WHITENOISE_MAX_AGE = 60 * 60 * 24 * 30
WHITENOISE_USE_FINDERS = True
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
# ---------------------------------------------------------------------------
# Sessions (the shopping cart lives here)
# ---------------------------------------------------------------------------

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14

# Backed by the database rather than local memory: Vercel's serverless
# functions do not share process memory (and often do not even reuse the
# same process) between requests, so an in-memory cache would silently fail
# to rate-limit anything. Used for the dashboard login throttle in
# store/dashboard.py; see migration 0008 for the table it needs.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}
SESSION_SAVE_EVERY_REQUEST = False


# ---------------------------------------------------------------------------
# Security (only enforced outside DEBUG so local http:// still works)
# ---------------------------------------------------------------------------

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"


# ---------------------------------------------------------------------------
# Store / orders
# ---------------------------------------------------------------------------

STORE_CURRENCY = os.environ.get("STORE_CURRENCY", "EGP")
STORE_CURRENCY_LABEL = os.environ.get("STORE_CURRENCY_LABEL", "EGP")
# Flat shipping fee, and the order subtotal above which shipping is free.
# These are the Egypt values; see STORE_COUNTRIES below for other countries.
STORE_SHIPPING_FEE = os.environ.get("STORE_SHIPPING_FEE", "60")
STORE_FREE_SHIPPING_THRESHOLD = os.environ.get("STORE_FREE_SHIPPING_THRESHOLD", "1500")
STORE_MAX_ITEM_QUANTITY = int(os.environ.get("STORE_MAX_ITEM_QUANTITY", "10"))
STORE_WHATSAPP_NUMBER = os.environ.get("STORE_WHATSAPP_NUMBER", "966578079833")

# ---------------------------------------------------------------------------
# HanyFit Meals
# ---------------------------------------------------------------------------
# Meals are priced in EGP only for now and are not run through the
# multi-country converter above (see STORE_COUNTRIES) -- there is no
# exchange-rate/shipping data for meals outside Egypt yet. Buying meals is
# simply unavailable while the delivery country is set to anything else.
MEALS_EGYPT_ONLY = env_bool("MEALS_EGYPT_ONLY", default=True)

# Grams of protein at/above which a meal counts as "High Protein" for the
# meal-catalogue filter.
MEALS_HIGH_PROTEIN_THRESHOLD_G = int(os.environ.get("MEALS_HIGH_PROTEIN_THRESHOLD_G", "25"))

# Per-meal cap on the quantity a single cart line can hold. The *daily
# delivery capacity* (Meal.daily_capacity / MealDailyAvailability) is a
# separate, date-specific limit enforced at checkout -- this is just a sane
# ceiling before a delivery date is even chosen, the same role
# STORE_MAX_ITEM_QUANTITY plays for apparel.
MEALS_MAX_CART_QUANTITY = int(os.environ.get("MEALS_MAX_CART_QUANTITY", "10"))

# How many days ahead customers may pick a delivery date at checkout / when
# starting a subscription.
MEALS_DELIVERY_WINDOW_DAYS = int(os.environ.get("MEALS_DELIVERY_WINDOW_DAYS", "14"))

# Calorie Calculator tuning (see meals/services/calculator.py for the
# formula). Adjustable without a code change if real-world feedback says the
# estimate runs high or low.
MEALS_CALCULATOR_ACTIVITY_FACTOR = os.environ.get("MEALS_CALCULATOR_ACTIVITY_FACTOR", "1.45")
MEALS_CALCULATOR_NEUTRAL_CONSTANT = os.environ.get("MEALS_CALCULATOR_NEUTRAL_CONSTANT", "-78")

# ---------------------------------------------------------------------------
# Delivery countries
# ---------------------------------------------------------------------------
# Every country the store ships to, keyed by ISO code. Product prices are
# entered once in the admin, in Egypt's currency (EGP) -- that is the "base"
# price. Every other country's displayed/charged price is that base price
# multiplied by "exchange_rate" and rounded to two decimals. There is no live
# FX lookup here on purpose (one less thing that can fail mid-checkout), so
# the rate is a manually maintained number. Update it (or the matching
# environment variable) whenever the real rate drifts meaningfully.
#
# "has_regions" controls whether the checkout page shows the governorate
# dropdown. Egypt has one (see GOVERNORATES in store/forms.py); Saudi Arabia
# deliberately does not -- checkout only asks for a city and a full address.
STORE_DEFAULT_COUNTRY = os.environ.get("STORE_DEFAULT_COUNTRY", "EG")

STORE_COUNTRIES = {
    "EG": {
        "label": "Egypt",
        "currency": STORE_CURRENCY,
        "exchange_rate": Decimal("1"),
        "shipping_fee": Decimal(os.environ.get("STORE_SHIPPING_FEE_EG", STORE_SHIPPING_FEE)),
        "free_shipping_threshold": Decimal(
            os.environ.get("STORE_FREE_SHIPPING_THRESHOLD_EG", STORE_FREE_SHIPPING_THRESHOLD)
        ),
        "has_regions": True,
    },
    "SA": {
        "label": "Saudi Arabia",
        "currency": os.environ.get("STORE_CURRENCY_SA", "SAR"),
        # EGP -> SAR. Roughly 0.075-0.08 SAR per EGP at the time this was
        # written (manual estimate, not a live feed). Adjust via
        # STORE_SAR_EXCHANGE_RATE without touching code when it moves.
        "exchange_rate": Decimal(os.environ.get("STORE_SAR_EXCHANGE_RATE", "0.078")),
        "shipping_fee": Decimal(os.environ.get("STORE_SHIPPING_FEE_SA", "25")),
        "free_shipping_threshold": Decimal(
            os.environ.get("STORE_FREE_SHIPPING_THRESHOLD_SA", "375")
        ),
        "has_regions": False,
    },
}


# ---------------------------------------------------------------------------
# Fawaterk payment gateway
# ---------------------------------------------------------------------------
# FAWATERK_VENDOR_KEY is the API key from Dashboard > Integrations > Fawaterak.
# The same key is the HMAC secret used to sign webhooks, so it must stay server
# side only and never be rendered into a template.

# Credentials live in test_project/credentials.py, read only from the
# environment. Each one still honours an environment variable of the same
# name, so any single value can be moved to Vercel's environment settings
# without a code change.
FAWATERK_CLIENT_ID = credentials.FAWATERK_CLIENT_ID
FAWATERK_CLIENT_SECRET = credentials.FAWATERK_CLIENT_SECRET
FAWATERK_TOKEN_URL = credentials.FAWATERK_TOKEN_URL

# The HMAC secret for webhook verification, and (confirmed by a live request
# against the production account) the Bearer credential createInvoiceLink and
# getInvoiceData actually accept. See store/services/fawaterk.py.
FAWATERK_HASH_KEY = credentials.FAWATERK_HASH_KEY
FAWATERK_REFUND_WEBHOOK_TOKEN = credentials.FAWATERK_REFUND_WEBHOOK_TOKEN

FAWATERK_LIVE = credentials.FAWATERK_LIVE
FAWATERK_BASE_URL = credentials.FAWATERK_API_BASE_URL
FAWATERK_TIMEOUT = int(os.environ.get("FAWATERK_TIMEOUT", "20"))

# Reject webhooks whose HMAC does not match. Only disable while debugging.
FAWATERK_VERIFY_WEBHOOK = env_bool("FAWATERK_VERIFY_WEBHOOK", default=True)

# TEMPORARY: logs every Fawaterk request/response (Authorization header
# masked) to the "store" logger at INFO level, so a rejected invoice can be
# diagnosed from the Vercel log. Turn off (FAWATERK_DEBUG_LOGGING=0) once live
# payments are confirmed stable -- the request log includes the customer's
# name, phone and address.
FAWATERK_DEBUG_LOGGING = env_bool("FAWATERK_DEBUG_LOGGING", default=True)


# ---------------------------------------------------------------------------
# TikTok pixel and Events API
# ---------------------------------------------------------------------------

TIKTOK_PIXEL_ID = credentials.TIKTOK_PIXEL_ID
FACEBOOK_PIXEL_ID = credentials.FACEBOOK_PIXEL_ID
TIKTOK_ACCESS_TOKEN = credentials.TIKTOK_ACCESS_TOKEN
TIKTOK_API_URL = os.environ.get(
    "TIKTOK_API_URL",
    "https://business-api.tiktok.com/open_api/v1.3/event/track/",
)
TIKTOK_TEST_EVENT_CODE = os.environ.get("TIKTOK_TEST_EVENT_CODE", "")
TIKTOK_TIMEOUT = int(os.environ.get("TIKTOK_TIMEOUT", "10"))
# Used to turn local numbers (01012345678) into E.164 before hashing.
TIKTOK_DEFAULT_COUNTRY_CODE = os.environ.get("TIKTOK_DEFAULT_COUNTRY_CODE", "20")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Vercel captures stdout/stderr, so console logging is what shows in the
# deployment logs. Payment and tracking events log under the "store" logger.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "store": {
            "handlers": ["console"],
            "level": os.environ.get("STORE_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Email (order notifications)
# ---------------------------------------------------------------------------
# A full order summary is emailed to the shop owner as soon as a payment is
# verified. Until SMTP is configured we fall back to the console backend, so
# the message is written to the server log rather than raising an error and
# taking the webhook down with it.

ORDER_NOTIFICATION_EMAIL = credentials.ORDER_NOTIFICATION_EMAIL
ORDER_NOTIFICATION_CC = credentials.ORDER_NOTIFICATION_CC
NOTIFY_ON_COD_ORDER = credentials.NOTIFY_ON_COD_ORDER

EMAIL_HOST = credentials.EMAIL_HOST
EMAIL_PORT = credentials.EMAIL_PORT
EMAIL_HOST_USER = credentials.EMAIL_HOST_USER
EMAIL_HOST_PASSWORD = credentials.EMAIL_HOST_PASSWORD
EMAIL_USE_TLS = credentials.EMAIL_USE_TLS
EMAIL_USE_SSL = credentials.EMAIL_USE_SSL
DEFAULT_FROM_EMAIL = credentials.DEFAULT_FROM_EMAIL or "orders@hanyfit.com"
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# A mail server that hangs must not hang the payment webhook with it.
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "15"))

if EMAIL_HOST and EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
