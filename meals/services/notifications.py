"""Customer-facing notifications for HanyFit Meals order/subscription events.

This is deliberately separate from store/services/notifications.py, which
emails the *shop owner* about a new order landing -- this module emails the
*customer* about what's happening to their own order or subscription.

Channel is abstracted on purpose (see CHANNELS / notify(channels=...)) so
WhatsApp or SMS can be added later by registering one more function here,
without touching any of the call sites in store/views.py, store/dashboard.py
or meals/views.py/services/subscriptions.py. Only "email" is wired up today,
because that's the only channel this project already has configured
(test_project/settings.py EMAIL_*) -- see the audit note in the chat about
not claiming an integration that isn't real.

Sending is always best-effort: a notification failure must never turn a
successful order/subscription action into an error response, so every
public function here catches broadly and just logs.

Reminder-style events driven by the calendar rather than a user action
("upcoming delivery tomorrow") are NOT implemented -- this project has no
scheduled-task runner (no Celery/cron) to fire them from. notify() is ready
to be called from one once that infrastructure exists.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

# event -> (subject, body template). Templates are simple .format() strings
# on purpose -- there are ~15 of these and a full template-file-per-event
# would be pure boilerplate for one paragraph of text each.
EVENTS = {
    "order_received": (
        "We've got your order",
        "Hi {name},\n\nThanks for your HanyFit Meals order {ref}. "
        "We'll let you know as it's prepared and on its way.",
    ),
    "order_paid": (
        "Payment confirmed",
        "Hi {name},\n\nPayment for order {ref} is confirmed. Thank you!",
    ),
    "order_preparing": (
        "Your order is being prepared",
        "Hi {name},\n\nOrder {ref} is now being prepared in the kitchen.",
    ),
    "order_out_for_delivery": (
        "Your order is on its way",
        "Hi {name},\n\nOrder {ref} is out for delivery.",
    ),
    "order_delivered": (
        "Order delivered",
        "Hi {name},\n\nOrder {ref} has been delivered. Enjoy your meal!",
    ),
    "order_cancelled": (
        "Order cancelled",
        "Hi {name},\n\nOrder {ref} has been cancelled. If this is unexpected, please contact us.",
    ),
    "subscription_created": (
        "Subscription request received",
        "Hi {name},\n\nWe've received your {ref} subscription request. "
        "We'll confirm before your first delivery.",
    ),
    "subscription_paid": (
        "Subscription payment confirmed",
        "Hi {name},\n\nPayment for your subscription is confirmed. Welcome aboard!",
    ),
    "subscription_paused": (
        "Subscription paused",
        "Hi {name},\n\nYour subscription is paused. Deliveries will stop until you resume it.",
    ),
    "subscription_resumed": (
        "Subscription resumed",
        "Hi {name},\n\nYour subscription is active again.",
    ),
    "subscription_renewed": (
        "Subscription renewed",
        "Hi {name},\n\nYour subscription has been renewed. Thanks for staying with us!",
    ),
    "subscription_cancelled": (
        "Subscription cancelled",
        "Hi {name},\n\nYour subscription has been cancelled.",
    ),
}


def _send_email(to_email, subject, body):
    if not to_email:
        return False
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=True)
    return True


CHANNELS = {"email": _send_email}


def notify(event, *, to_email, name="", ref="", channels=("email",)):
    """Best-effort; never raises. Returns True if at least one channel ran."""
    spec = EVENTS.get(event)
    if not spec:
        logger.warning("Unknown customer notification event: %s", event)
        return False

    subject, template = spec
    body = template.format(name=name or "there", ref=ref)

    sent = False
    for channel in channels:
        sender = CHANNELS.get(channel)
        if not sender:
            logger.warning("Unknown notification channel: %s", channel)
            continue
        try:
            sent = sender(to_email, subject, body) or sent
        except Exception:
            logger.exception("Customer notification failed (event=%s, channel=%s)", event, channel)
    return sent
