"""Order notification emails.

The shop owner gets a full order summary the moment a payment is verified.

Two rules govern everything here:

1. Sending must never raise. This runs inside the payment webhook, and a
   mail server that is down or slow must not cause the webhook to return an
   error, because Fawaterk would then retry the same event indefinitely and
   the order could be processed twice.
2. Sending must happen once per order. ``Order.mark_paid()`` only returns True
   the first time, and that return value is what gates the email, so duplicate
   webhook deliveries do not produce duplicate emails.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


def _recipients():
    to = [settings.ORDER_NOTIFICATION_EMAIL] if settings.ORDER_NOTIFICATION_EMAIL else []
    cc = [
        address.strip()
        for address in (settings.ORDER_NOTIFICATION_CC or "").split(",")
        if address.strip()
    ]
    return to, cc


def is_configured():
    return bool(settings.ORDER_NOTIFICATION_EMAIL)


def build_context(order, reason="paid"):
    """Everything the templates need, resolved once."""
    items = list(order.items.all())
    return {
        "order": order,
        "items": items,
        "reason": reason,
        "item_count": sum(item.quantity for item in items),
        "placed_at": timezone.localtime(order.created_at),
        "paid_at": timezone.localtime(order.paid_at) if order.paid_at else None,
        "now": timezone.localtime(),
        "site_url": settings.SITE_URL.rstrip("/"),
        "currency": order.currency or settings.STORE_CURRENCY,
    }


def send_order_notification(order, reason="paid"):
    """Email the owner a full breakdown of ``order``.

    ``reason`` is "paid" for a verified online payment, or "cod" for a new
    cash-on-delivery order. Returns True if the message was handed to the mail
    backend.
    """
    to, cc = _recipients()

    if not to:
        # No mailbox configured yet. Log the essentials so the order is still
        # recoverable from the server log rather than lost.
        logger.warning(
            "ORDER_NOTIFICATION_EMAIL is not set. Order %s (%s, %s %s, %s) "
            "was not emailed.",
            order.order_number,
            order.full_name,
            order.total_price,
            order.currency,
            order.get_payment_status_display(),
        )
        return False

    context = build_context(order, reason)

    if reason == "paid":
        subject = (
            f"PAID - Order {order.order_number} - "
            f"{order.total_price} {order.currency} - {order.full_name}"
        )
    else:
        subject = (
            f"NEW COD Order {order.order_number} - "
            f"{order.total_price} {order.currency} - {order.full_name}"
        )

    try:
        text_body = render_to_string("store/email/order_notification.txt", context)
        html_body = render_to_string("store/email/order_notification.html", context)

        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to,
            cc=cc or None,
            # Replying to the notification reaches the customer directly.
            reply_to=[order.email] if order.email else None,
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)

    except Exception as exc:
        # Deliberately broad. See the module docstring: this runs inside the
        # payment webhook and must not be able to fail it.
        logger.exception(
            "Could not email the notification for order %s: %s",
            order.order_number,
            exc,
        )
        return False

    logger.info(
        "Order notification for %s emailed to %s",
        order.order_number,
        ", ".join(to + cc),
    )
    return True
