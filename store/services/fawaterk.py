"""Fawaterk (Fawaterak) payment gateway client.

Reference: https://fawaterak-api.readme.io/reference/sendpayment

Flow used by this project
------------------------
1. ``create_invoice(order)`` posts the order to ``/api/v2/createInvoiceLink``
   and gets back a hosted checkout URL. The customer is redirected there.
2. Fawaterk calls our webhook when the invoice is paid or fails. The callback
   carries an HMAC-SHA256 ``hashKey`` which we recompute and compare before
   trusting anything in the body.
3. Because a browser redirect can be forged or simply lost, the success page
   also calls ``get_invoice_data()`` and confirms ``paid == 1`` server side.
   The webhook is the source of truth; the redirect check is the safety net.

Authentication
--------------
Confirmed by a live request against this project's own production account
(2026-07): ``createInvoiceLink`` accepts ``Authorization: Bearer
{FAWATERK_HASH_KEY}`` (HTTP 200, invoice created) and rejects a Bearer token
obtained from the OAuth client-credentials endpoint (HTTP 400, "Invalid Token
or inactive vendor"), even though that OAuth endpoint itself works and issues
a token. So for this endpoint, on this account, the static hash/API key is
the credential to send. That does not mean OAuth is unsupported by Fawaterk
in general -- ``fetch_access_token()`` below is kept working and available in
case another Fawaterk product needs it later. It is simply not called from
``_bearer_token()`` any more.

The vendor key doubles as the HMAC secret, so it must never reach a template.
"""

import hashlib
import hmac
import logging
import re
import time
from decimal import Decimal

import requests
from django.conf import settings

from ..pricing import to_money

logger = logging.getLogger(__name__)

# Fawaterk restricts customer fields to alphanumerics plus a small punctuation
# set. Unicode letters are kept so Arabic names pass through unchanged.
_NAME_ALLOWED = re.compile(r"[^\w@\-. ]", flags=re.UNICODE)
_ADDRESS_ALLOWED = re.compile(r"[^\w@\-.,:/ ]", flags=re.UNICODE)


class FawaterkError(Exception):
    """Raised when an invoice cannot be created."""

    def __init__(self, message, *, payload=None, status_code=None):
        super().__init__(message)
        self.payload = payload
        self.status_code = status_code


# In-process cache for the OAuth access token. Serverless functions are
# short-lived, so this mostly saves repeat calls within one warm instance
# rather than acting as a long-term store.
_token_cache = {"access_token": "", "expires_at": 0.0}


def is_configured():
    """True when we have the credential createInvoiceLink actually accepts."""
    return bool(settings.FAWATERK_HASH_KEY)


def has_webhook_secret():
    return bool(settings.FAWATERK_HASH_KEY)


def reset_token_cache():
    """Drop the cached token. Used by tests and after a 401."""
    _token_cache["access_token"] = ""
    _token_cache["expires_at"] = 0.0


def fetch_access_token(force=False):
    """Get an OAuth2 access token using the client-credentials grant.

    Fawaterk exposes a Laravel Passport token endpoint. We post the client id
    and secret and get back a bearer token with a lifetime in seconds. The
    token is cached until 60 seconds before it expires, so a long-lived process
    is not re-authenticating on every request.

    This call itself works (confirmed live). Its output is just not accepted
    by createInvoiceLink/getInvoiceData -- see the module docstring. Kept
    available for a future Fawaterk product that may require it; nothing in
    this module calls it right now.

    Returns the token string, or "" if the grant is unavailable.
    """
    if not (settings.FAWATERK_CLIENT_ID and settings.FAWATERK_CLIENT_SECRET):
        return ""

    now = time.time()
    if not force and _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    try:
        response = requests.post(
            settings.FAWATERK_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.FAWATERK_CLIENT_ID,
                "client_secret": settings.FAWATERK_CLIENT_SECRET,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=settings.FAWATERK_TIMEOUT,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Could not obtain a Fawaterk access token: %s", exc)
        return ""

    token = body.get("access_token") or ""
    if not token:
        logger.error(
            "Fawaterk token endpoint returned no access_token (HTTP %s): %s",
            response.status_code,
            str(body)[:300],
        )
        return ""

    # Default to an hour if the server does not say.
    try:
        expires_in = int(body.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600

    # Cache until 60 seconds before expiry, so a token cannot be used after it
    # has lapsed. If the lifetime is shorter than that margin, do not cache at
    # all: a floor here would mean happily reusing an already-expired token.
    safe_lifetime = expires_in - 60
    if safe_lifetime > 0:
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = now + safe_lifetime
    else:
        reset_token_cache()

    logger.info("Obtained a Fawaterk access token (valid %ss)", expires_in)
    return token


def _bearer_token():
    """The token to send on API calls.

    Always the static hash/API key -- confirmed by a live request against
    this project's account to be what createInvoiceLink actually accepts.
    See the module docstring for why this is no longer sourced from
    ``fetch_access_token()``.
    """
    return settings.FAWATERK_HASH_KEY


def _headers():
    return {
        "Authorization": f"Bearer {_bearer_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Temporary debug logging
# ---------------------------------------------------------------------------
# Controlled by FAWATERK_DEBUG_LOGGING (default on). Logs the exact request
# and response for every call to createInvoiceLink / getInvoiceData, with the
# Authorization header masked, so a rejected invoice can be diagnosed from the
# Vercel log. Turn off (FAWATERK_DEBUG_LOGGING=0) once live payments are
# confirmed stable -- the request log includes the customer's name, phone and
# address.

def _mask(value, keep=4):
    value = value or ""
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}...{value[-keep:]}"


def _debug_request(label, method, url, headers, json_body):
    if not getattr(settings, "FAWATERK_DEBUG_LOGGING", False):
        return
    safe_headers = dict(headers)
    if "Authorization" in safe_headers:
        scheme, _, token = safe_headers["Authorization"].partition(" ")
        safe_headers["Authorization"] = f"{scheme} {_mask(token)}"
    logger.info(
        "[fawaterk debug] %s request: %s %s\nheaders=%s\nbody=%s",
        label, method, url, safe_headers, json_body,
    )


def _debug_response(label, response):
    if not getattr(settings, "FAWATERK_DEBUG_LOGGING", False):
        return
    try:
        body = response.text[:2000]
    except Exception:  # pragma: no cover - defensive only
        body = "<unreadable body>"
    logger.info(
        "[fawaterk debug] %s response: HTTP %s\nbody=%s",
        label, response.status_code, body,
    )


def _clean_name(value, fallback="Customer"):
    cleaned = _NAME_ALLOWED.sub(" ", (value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:60] or fallback


def _clean_address(value):
    cleaned = _ADDRESS_ALLOWED.sub(" ", (value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:250] or "N/A"


def _clean_phone(value):
    """Digits (and a leading +) only, which is what the gateway accepts."""
    digits = re.sub(r"[^\d+]", "", value or "")
    return digits[:20]


def split_name(full_name):
    """Fawaterk requires separate first and last names."""
    if not (full_name or "").strip():
        return "Customer", "Order"
    parts = [p for p in _clean_name(full_name).split(" ") if p]
    if not parts:
        return "Customer", "Order"
    if len(parts) == 1:
        # last_name is mandatory, so mirror the first name rather than send "".
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])[:60]


def _absolute(path):
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def create_invoice(order, *, success_url, fail_url, pending_url, webhook_url=None):
    """Create a hosted invoice for ``order`` and return the redirect details.

    Returns a dict: ``{"url": ..., "invoice_id": ..., "invoice_key": ...}``.
    Raises ``FawaterkError`` on any failure, including network problems, so the
    caller can keep the order as pending and show a retry instead of a 500.
    """
    if not is_configured():
        raise FawaterkError("Fawaterk credentials are not configured.")

    first_name, last_name = split_name(order.full_name)

    cart_items = []
    for item in order.items.all():
        cart_items.append(
            {
                "name": _clean_name(item.product_name, fallback="Item")
                + f" ({item.size})",
                "price": str(to_money(item.unit_price)),
                "quantity": str(item.quantity),
            }
        )

    if not cart_items:
        raise FawaterkError("Cannot create an invoice for an order with no items.")

    # cartTotal must equal the sum of cartItems -- confirmed by a live
    # request against the production account: sending subtotal+shipping
    # (185.00, matching order.total_price) got HTTP 422 "Your cart total
    # doesn't match the items total"; sending subtotal alone (125.00) with
    # shipping as its own field got HTTP 200 and a real invoice. The
    # customer's total is unaffected -- shipping still travels in the
    # "shipping" field below, and order.total_price/subtotal in the database
    # are untouched. This only changes what this one payload sends.
    shipping = to_money(order.shipping_cost)
    total = to_money(order.subtotal)

    payload = {
        "cartTotal": str(total),
        "currency": order.currency or settings.STORE_CURRENCY,
        "customer": {
            "first_name": first_name,
            "last_name": last_name,
            "phone": _clean_phone(order.phone),
            "address": _clean_address(
                ", ".join(
                    part for part in (order.address, order.city, order.governorate) if part
                )
            ),
        },
        "cartItems": cart_items,
        # Echoed back on the webhook, which is how we find the order again
        # without trusting a query string.
        "payLoad": {
            "order_number": order.order_number,
            "order_id": order.pk,
        },
        "redirectionUrls": {
            "successUrl": success_url,
            "failUrl": fail_url,
            "pendingUrl": pending_url,
        },
        "sendEmail": bool(order.email),
        "sendSMS": False,
    }

    if order.email:
        payload["customer"]["email"] = order.email
    if shipping > Decimal("0"):
        payload["shipping"] = str(shipping)
    if webhook_url:
        # Overrides the dashboard setting, so staging and production can point
        # at their own deployments without reconfiguring the portal.
        payload["redirectionUrls"]["webhookUrl"] = webhook_url

    url = f"{settings.FAWATERK_BASE_URL}/api/v2/createInvoiceLink"
    headers = _headers()

    _debug_request("createInvoiceLink", "POST", url, headers, payload)

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=settings.FAWATERK_TIMEOUT,
        )
    except requests.Timeout as exc:
        # repr(exc) here is diagnostic only -- never shown to the customer,
        # the FawaterkError message below is unchanged. Distinguishing the
        # real exception type (Timeout vs SSLError vs ConnectionError vs
        # anything else) is what "Could not reach the payment provider" was
        # previously hiding.
        logger.error("Fawaterk request timed out: %r", exc)
        raise FawaterkError("The payment provider timed out.") from exc
    except requests.exceptions.SSLError as exc:
        logger.error("Fawaterk TLS/SSL error: %r", exc)
        raise FawaterkError("Could not reach the payment provider.") from exc
    except requests.exceptions.ConnectionError as exc:
        # Covers DNS failure, connection refused, and proxy errors -- requests
        # wraps all of them in ConnectionError; repr(exc) shows which.
        logger.error("Fawaterk connection error: %r", exc)
        raise FawaterkError("Could not reach the payment provider.") from exc
    except requests.RequestException as exc:
        logger.error("Fawaterk request failed (%s): %r", type(exc).__name__, exc)
        raise FawaterkError("Could not reach the payment provider.") from exc

    _debug_response("createInvoiceLink", response)

    try:
        body = response.json()
    except ValueError:
        logger.error(
            "Fawaterk returned non-JSON (HTTP %s): %s",
            response.status_code,
            response.text[:500],
        )
        raise FawaterkError(
            "The payment provider returned an unexpected response.",
            status_code=response.status_code,
        )

    if response.status_code >= 400 or body.get("status") != "success":
        message = _extract_error(body)
        logger.error(
            "Fawaterk invoice failed for order %s (HTTP %s): %s",
            order.order_number,
            response.status_code,
            body,
        )
        raise FawaterkError(message, payload=body, status_code=response.status_code)

    data = body.get("data") or {}
    invoice_url = data.get("url")
    if not invoice_url:
        raise FawaterkError("The payment provider did not return a checkout link.",
                            payload=body)

    return {
        "url": invoice_url,
        "invoice_id": str(data.get("invoiceId") or ""),
        "invoice_key": str(data.get("invoiceKey") or ""),
    }


def _extract_error(body):
    """Pull a human-usable message out of Fawaterk's several error shapes."""
    if not isinstance(body, dict):
        return "The payment provider rejected the request."

    message = body.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()[:300]

    errors = body.get("data") if isinstance(body.get("data"), dict) else body
    if isinstance(errors, dict):
        for value in errors.values():
            if isinstance(value, list) and value:
                return str(value[0])[:300]
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]

    return "The payment provider rejected the request."


def get_invoice_data(invoice_id):
    """Fetch an invoice's current state. Returns the ``data`` dict or None."""
    if not is_configured() or not invoice_id:
        return None

    url = f"{settings.FAWATERK_BASE_URL}/api/v2/getInvoiceData/{invoice_id}"
    headers = _headers()
    _debug_request("getInvoiceData", "GET", url, headers, None)
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=settings.FAWATERK_TIMEOUT,
        )
        _debug_response("getInvoiceData", response)
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Could not read Fawaterk invoice %s: %s", invoice_id, exc)
        return None

    if body.get("status") != "success":
        logger.warning("Fawaterk invoice %s lookup failed: %s", invoice_id, body)
        return None

    return body.get("data") or {}


def is_invoice_paid(invoice_id):
    """Authoritative paid check, used to confirm the success redirect."""
    data = get_invoice_data(invoice_id)
    if not data:
        return False, None
    paid = str(data.get("paid", "0")).strip().lower() in {"1", "true", "yes"}
    return paid, data


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def _hmac_hex(query_param):
    # Signed with the Hash API Key from the Fawaterk dashboard, never with the
    # OAuth token (which rotates and would make signatures unverifiable).
    return hmac.new(
        settings.FAWATERK_HASH_KEY.encode("utf-8"),
        query_param.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _matches(expected, received):
    if not expected or not received:
        return False
    # Constant-time compare: a plain == leaks how much of the hash matched.
    return hmac.compare_digest(expected.lower(), str(received).strip().lower())


def verify_invoice_hash(payload):
    """Verify a paid or failed callback.

    Signed string, per Fawaterk's docs:
        InvoiceId=<invoice_id>&InvoiceKey=<invoice_key>&PaymentMethod=<payment_method>
    """
    if not has_webhook_secret():
        return False
    query_param = (
        f"InvoiceId={payload.get('invoice_id', '')}"
        f"&InvoiceKey={payload.get('invoice_key', '')}"
        f"&PaymentMethod={payload.get('payment_method', '')}"
    )
    return _matches(_hmac_hex(query_param), payload.get("hashKey"))


def verify_expired_hash(payload):
    """Verify a Fawry/Aman/Masary expiry callback.

    Signed string:
        referenceId=<referenceId>&PaymentMethod=<paymentMethod>
    """
    if not has_webhook_secret():
        return False
    query_param = (
        f"referenceId={payload.get('referenceId', '')}"
        f"&PaymentMethod={payload.get('paymentMethod', '')}"
    )
    return _matches(_hmac_hex(query_param), payload.get("hashKey"))