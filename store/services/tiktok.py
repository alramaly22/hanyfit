"""TikTok Pixel and Events API (2.0) helpers.

Reference: https://business-api.tiktok.com/open_api/v1.3/event/track/

Why both layers
---------------
The browser pixel is easy to install but loses events to ad blockers, iOS
tracking restrictions and abandoned tabs. The Events API reports from the
server, where nothing can block it, and it is the only reliable way to report a
purchase that completes on the gateway's domain rather than ours.

Deduplication is the part people get wrong: when the same conversion is
reported by both layers, TikTok merges them only if they share an ``event_id``.
Without that, every purchase is counted twice and every optimisation decision
is made on doubled data. So each order gets one generated event id, stored on
the order, used by both the pixel and the API call.

A note on event names: TikTok's standard purchase event is ``CompletePayment``,
not ``Purchase`` (that is Meta's name). ``PURCHASE`` below maps to the correct
TikTok name so reporting and campaign optimisation actually pick it up.
"""

import hashlib
import logging
import re
import secrets
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Standard TikTok web events used by this store.
VIEW_CONTENT = "ViewContent"
ADD_TO_CART = "AddToCart"
INITIATE_CHECKOUT = "InitiateCheckout"
ADD_PAYMENT_INFO = "AddPaymentInfo"
PLACE_AN_ORDER = "PlaceAnOrder"
PURCHASE = "CompletePayment"


def is_pixel_enabled():
    return bool(settings.TIKTOK_PIXEL_ID)


def is_events_api_enabled():
    return bool(settings.TIKTOK_PIXEL_ID and settings.TIKTOK_ACCESS_TOKEN)


def new_event_id():
    """Unique id shared by the pixel and the server event for one action."""
    return secrets.token_hex(16)


# ---------------------------------------------------------------------------
# Identifier normalisation and hashing
# ---------------------------------------------------------------------------

def _sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_email(email):
    """Lowercase and trim before hashing, as TikTok requires."""
    if not email:
        return ""
    normalised = email.strip().lower()
    if "@" not in normalised:
        return ""
    return _sha256(normalised)


def hash_phone(phone, default_country_code=None):
    """Normalise to E.164 (no '+') and hash.

    Local Egyptian numbers such as 01012345678 must become 201012345678, or
    TikTok cannot match them against its user base and the event match quality
    score drops.
    """
    if not phone:
        return ""

    country_code = default_country_code or getattr(
        settings, "TIKTOK_DEFAULT_COUNTRY_CODE", "20"
    )

    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""

    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = f"{country_code}{digits[1:]}"
    elif not digits.startswith(country_code) and len(digits) <= 10:
        digits = f"{country_code}{digits}"

    if len(digits) < 8:
        return ""
    return _sha256(f"+{digits}")


def client_ip(request):
    """Best-effort client IP behind Vercel's proxy."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def user_payload(request, *, email="", phone="", external_id=""):
    """Assemble the ``user`` block, omitting anything we do not have.

    ``ttclid`` (click id) and ``ttp`` (pixel cookie) are what tie a conversion
    back to the ad that caused it, so they are pulled from the query string and
    cookies when present.
    """
    data = {}

    hashed_email = hash_email(email)
    if hashed_email:
        data["email"] = hashed_email

    hashed_phone = hash_phone(phone)
    if hashed_phone:
        data["phone"] = hashed_phone

    if external_id:
        data["external_id"] = _sha256(str(external_id).strip().lower())

    if request is not None:
        ttclid = request.GET.get("ttclid") or request.COOKIES.get("ttclid")
        if ttclid:
            data["ttclid"] = ttclid

        ttp = request.COOKIES.get("_ttp")
        if ttp:
            data["ttp"] = ttp

        ip = client_ip(request)
        if ip:
            data["ip"] = ip

        agent = request.META.get("HTTP_USER_AGENT", "")
        if agent:
            data["user_agent"] = agent

    return data


def page_payload(request):
    if request is None:
        return {}
    payload = {"url": request.build_absolute_uri()}
    referrer = request.META.get("HTTP_REFERER")
    if referrer:
        payload["referrer"] = referrer
    return payload


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_event(
    event_name,
    *,
    event_id=None,
    request=None,
    properties=None,
    user=None,
    event_time=None,
):
    """POST a single event to the Events API.

    Never raises. Analytics must not be able to break a checkout, so every
    failure is logged and swallowed; the browser pixel still covers the event.
    Returns True only if TikTok acknowledged with code 0.
    """
    if not is_events_api_enabled():
        return False

    payload = {
        "event_source": "web",
        "event_source_id": settings.TIKTOK_PIXEL_ID,
        "data": [
            {
                "event": event_name,
                "event_time": int(event_time or time.time()),
                "event_id": event_id or new_event_id(),
                "user": user if user is not None else user_payload(request),
                "page": page_payload(request),
                "properties": properties or {},
            }
        ],
    }

    if settings.TIKTOK_TEST_EVENT_CODE:
        payload["test_event_code"] = settings.TIKTOK_TEST_EVENT_CODE

    try:
        response = requests.post(
            settings.TIKTOK_API_URL,
            json=payload,
            headers={
                "Access-Token": settings.TIKTOK_ACCESS_TOKEN,
                "Content-Type": "application/json",
            },
            timeout=settings.TIKTOK_TIMEOUT,
        )
        body = response.json()
    except Exception as exc:
        # Deliberately broad: a tracking problem must never surface as a failed
        # checkout. The browser pixel still reports the event.
        logger.warning("TikTok Events API call failed (%s): %s", event_name, exc)
        return False

    if body.get("code") not in (0, "0"):
        logger.warning(
            "TikTok rejected %s: code=%s message=%s",
            event_name,
            body.get("code"),
            body.get("message"),
        )
        return False

    logger.info("TikTok event sent: %s (id=%s)", event_name, payload["data"][0]["event_id"])
    return True


def order_properties(order):
    """Purchase properties for a completed order."""
    contents = [
        {
            "content_id": item.product.slug if item.product else str(item.pk),
            "content_type": "product",
            "content_name": item.product_name,
            "quantity": item.quantity,
            "price": float(item.unit_price or 0),
        }
        for item in order.items.all()
    ]
    return {
        "currency": order.currency or settings.STORE_CURRENCY,
        "value": float(order.total_price or 0),
        "contents": contents,
        "content_type": "product",
        "order_id": order.order_number,
    }


def send_purchase(order, request=None):
    """Report a paid order. Idempotent: sends at most once per order."""
    if order.tiktok_purchase_sent or not is_events_api_enabled():
        return False

    event_id = order.tiktok_event_id or new_event_id()

    sent = send_event(
        PURCHASE,
        event_id=event_id,
        request=request,
        properties=order_properties(order),
        user=user_payload(
            request,
            email=order.email,
            phone=order.phone,
            external_id=order.order_number,
        ),
    )

    if sent:
        order.tiktok_event_id = event_id
        order.tiktok_purchase_sent = True
        order.save(update_fields=["tiktok_event_id", "tiktok_purchase_sent"])
    return sent
