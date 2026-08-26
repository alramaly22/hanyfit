"""Lightweight, privacy-conscious visitor tracking.

Logs at most one PageVisit row per (session, page) every ``DEDUPE_WINDOW``,
so repeated reloads or a bot hammering a page cannot inflate the numbers or
flood the database. Everything here is best-effort: an exception in tracking
must never turn into a broken page, so all of it is wrapped and logged
rather than allowed to propagate.
"""

import logging
from urllib.parse import urlparse

from django.utils import timezone

from . import geo
from .models import PageVisit

logger = logging.getLogger(__name__)

# Paths (or prefixes) that are never counted as a "visit": the dashboard and
# admin (staff activity, not customer traffic), static/media, and the
# JSON/webhook endpoints the cart and payment gateway call in the background.
_SKIP_PREFIXES = (
    "/admin/",
    "/dashboard/",
    "/static/",
    "/media/",
)
_SKIP_SUFFIXES = (
    "/webhook/",
    "/webhook_json/",
)

# How often the same browser session can register a new visit to the same
# path. Keeps a person reloading the page, or double-navigating, from being
# counted twice, without needing a separate "seen" table.
DEDUPE_WINDOW_SECONDS = 20 * 60

_MOBILE_HINTS = ("iphone", "android", "mobile", "ipod")
_TABLET_HINTS = ("ipad", "tablet")


def _device_type(user_agent):
    ua = (user_agent or "").lower()
    if any(hint in ua for hint in _TABLET_HINTS):
        return "tablet"
    if any(hint in ua for hint in _MOBILE_HINTS):
        return "mobile"
    if ua:
        return "desktop"
    return "other"


def _referrer_host(referrer, this_host):
    if not referrer:
        return ""
    try:
        host = urlparse(referrer).netloc
    except ValueError:
        return ""
    if not host or host == this_host:
        return ""  # internal navigation is not an acquisition source
    return host[:255]


class VisitTrackingMiddleware:
    """Records a PageVisit for real, successful page views."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._maybe_record(request, response)
        except Exception:
            # Tracking must never be able to break a response that has
            # already been generated successfully.
            logger.warning("Visit tracking failed", exc_info=True)
        return response

    def _maybe_record(self, request, response):
        if request.method != "GET":
            return
        if not (200 <= response.status_code < 400):
            return
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return

        path = request.path
        if path.startswith(_SKIP_PREFIXES) or path.endswith(_SKIP_SUFFIXES):
            return

        session = getattr(request, "session", None)
        if session is None:
            return
        if not session.session_key:
            session.save()  # forces a session key to exist for this visitor
        session_key = session.session_key or ""
        if not session_key:
            return

        if self._recently_seen(session_key, path):
            return

        PageVisit.objects.create(
            session_key=session_key,
            path=path[:255],
            referrer_host=_referrer_host(
                request.META.get("HTTP_REFERER", ""), request.get_host()
            ),
            device_type=_device_type(request.META.get("HTTP_USER_AGENT", "")),
            country=geo.get_country(request),
        )

    def _recently_seen(self, session_key, path):
        """True if this session already has a visit to this path within the
        de-dupe window. One extra indexed query, cheap compared to the
        alternative of a write on every single reload."""
        cutoff = timezone.now() - timezone.timedelta(seconds=DEDUPE_WINDOW_SECONDS)
        return PageVisit.objects.filter(
            session_key=session_key, path=path, created_at__gte=cutoff
        ).exists()
