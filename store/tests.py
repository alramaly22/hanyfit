"""Tests for the store.

The emphasis is on the things that cost real money if they break: cart pricing,
stock, payment verification and webhook handling.
"""

import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Order, OrderItem, Product, ProductVariant, WebhookEvent
from .services import fawaterk, notifications, tiktok

# The Hash API Key is what signs webhooks, so that is what the tests use.
HASH_KEY = "test-hash-key"

TEST_SETTINGS = dict(
    FAWATERK_HASH_KEY=HASH_KEY,
    FAWATERK_CLIENT_ID="test-client-id",
    FAWATERK_CLIENT_SECRET="test-client-secret",
    FAWATERK_TOKEN_URL="https://app.fawaterk.com/oauth/token",
    FAWATERK_DEBUG_LOGGING=False,
    ORDER_NOTIFICATION_EMAIL="owner@example.com",
    ORDER_NOTIFICATION_CC="",
    NOTIFY_ON_COD_ORDER=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FAWATERK_BASE_URL="https://staging.fawaterk.com",
    FAWATERK_VERIFY_WEBHOOK=True,
    TIKTOK_PIXEL_ID="",
    TIKTOK_ACCESS_TOKEN="",
    STORE_SHIPPING_FEE="60",
    STORE_FREE_SHIPPING_THRESHOLD="1000",
    STORE_MAX_ITEM_QUANTITY=10,
    STORE_CURRENCY="EGP",
    # settings.py enables SECURE_SSL_REDIRECT whenever DEBUG is False (see the
    # "Security" block there), which is exactly the state `manage.py test`
    # runs in unless the shell happens to have DJANGO_DEBUG=1 exported first.
    # Django's test Client makes plain http:// requests, so SecurityMiddleware
    # 301-redirected every single one of them to https:// before any view
    # ever ran -- the "many unrelated tests get 301" symptom. Overriding it
    # here makes `python manage.py test store` correct on its own, without
    # depending on the caller's shell state.
    SECURE_SSL_REDIRECT=False,
)


def make_product(slug="tee", price="450.00", stock=5, sizes=("S", "M", "L")):
    product = Product.objects.create(
        name=f"Product {slug}",
        slug=slug,
        description="A test garment.",
        price=Decimal(price),
        front_image="images/store/front.png",
        back_image="images/store/back.png",
    )
    for size in sizes:
        ProductVariant.objects.create(product=product, size=size, stock=stock)
    return product


def _tiktok_purchases(calls):
    """Pull CompletePayment event bodies out of a shared requests.post mock."""
    found = []
    for call in calls:
        payload = call.kwargs.get("json") or {}
        for event in payload.get("data", []):
            if event.get("event") == "CompletePayment":
                found.append(event)
    return found


def valid_paid_payload(order, invoice_id="12345", method="Card"):
    """Build a webhook body with a correctly computed hashKey."""
    invoice_key = "INVKEY123"
    query = f"InvoiceId={invoice_id}&InvoiceKey={invoice_key}&PaymentMethod={method}"
    signature = hmac.new(
        HASH_KEY.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "hashKey": signature,
        "invoice_id": invoice_id,
        "invoice_key": invoice_key,
        "payment_method": method,
        "invoice_status": "paid",
        "pay_load": {"order_number": order.order_number, "order_id": order.pk},
        "referenceNumber": "9988776655",
    }


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class CartTests(TestCase):
    def setUp(self):
        self.product = make_product(stock=3)

    def add(self, size="M", quantity=1, slug=None):
        return self.client.post(
            reverse("cart_add"),
            {"slug": slug or self.product.slug, "size": size, "quantity": quantity},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_add_to_cart_returns_updated_totals(self):
        response = self.add(quantity=2)
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["cart"]["count"], 2)
        self.assertEqual(Decimal(body["cart"]["subtotal"]), Decimal("900.00"))

    def test_quantity_is_clamped_to_available_stock(self):
        response = self.add(quantity=99)
        body = response.json()

        self.assertTrue(body["ok"])
        # Only 3 in stock, so that is what lands in the bag.
        self.assertEqual(body["cart"]["count"], 3)
        self.assertIn("3", body["message"])

    def test_cannot_add_an_unknown_size(self):
        response = self.add(size="XXXL")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_cannot_add_an_out_of_stock_size(self):
        ProductVariant.objects.filter(product=self.product, size="M").update(stock=0)
        response = self.add(size="M")
        self.assertEqual(response.status_code, 400)
        self.assertIn("sold out", response.json()["message"].lower())

    def test_cannot_add_an_inactive_product(self):
        self.product.is_active = False
        self.product.save(update_fields=["is_active"])

        response = self.add()
        self.assertEqual(response.status_code, 400)

    def test_price_comes_from_the_database_not_the_session(self):
        """A tampered session must not change what the customer is charged."""
        self.add(quantity=1)

        session = self.client.session
        key = list(session["cart"].keys())[0]
        session["cart"][key]["unit_price"] = "1.00"  # attacker-supplied
        session.save()

        response = self.client.get(reverse("cart"))
        self.assertContains(response, "450")

    def test_same_product_in_two_sizes_is_two_lines(self):
        self.add(size="S")
        self.add(size="M")

        response = self.client.get(reverse("cart"))
        self.assertEqual(len(response.context["cart"]), 2)

    def test_shipping_is_free_above_the_threshold(self):
        expensive = make_product(slug="hoodie", price="1200.00", stock=2)
        self.client.post(
            reverse("cart_add"),
            {"slug": expensive.slug, "size": "M", "quantity": 1},
        )

        response = self.client.get(reverse("cart"))
        self.assertEqual(response.context["shipping"], Decimal("0.00"))
        self.assertEqual(response.context["total"], Decimal("1200.00"))

    def test_shipping_is_charged_below_the_threshold(self):
        self.add(quantity=1)
        response = self.client.get(reverse("cart"))

        self.assertEqual(response.context["shipping"], Decimal("60.00"))
        self.assertEqual(response.context["total"], Decimal("510.00"))

    def test_line_is_dropped_when_the_product_is_deactivated(self):
        self.add()
        self.product.is_active = False
        self.product.save(update_fields=["is_active"])

        response = self.client.get(reverse("cart"))
        self.assertEqual(len(response.context["cart"]), 0)

    def test_remove_empties_the_bag(self):
        self.add()
        session = self.client.session
        key = list(session["cart"].keys())[0]

        response = self.client.post(
            reverse("cart_remove"),
            {"key": key},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.json()["cart"]["count"], 0)

    def test_update_to_zero_removes_the_line(self):
        self.add(quantity=2)
        session = self.client.session
        key = list(session["cart"].keys())[0]

        response = self.client.post(
            reverse("cart_update"),
            {"key": key, "quantity": 0},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.json()["cart"]["count"], 0)

    def test_cart_mutations_reject_get(self):
        self.assertEqual(self.client.get(reverse("cart_add")).status_code, 405)
        self.assertEqual(self.client.get(reverse("cart_remove")).status_code, 405)


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class CheckoutTests(TestCase):
    def setUp(self):
        self.product = make_product(stock=5)

    def fill_cart(self, quantity=2, size="M"):
        self.client.post(
            reverse("cart_add"),
            {"slug": self.product.slug, "size": size, "quantity": quantity},
        )

    def valid_form(self, **overrides):
        data = {
            "full_name": "Ahmed Hassan",
            "phone": "01012345678",
            "email": "ahmed@example.com",
            "governorate": "Cairo",
            "city": "Nasr City",
            "address": "12 Abbas El Akkad Street, floor 3, apartment 7",
            "notes": "",
            "payment": "cash",
        }
        data.update(overrides)
        return data

    def test_empty_cart_redirects_to_the_shop(self):
        response = self.client.get(reverse("checkout"))
        self.assertRedirects(response, reverse("store"))

    def test_cash_order_is_created_and_stock_reserved(self):
        self.fill_cart(quantity=2)
        response = self.client.post(reverse("checkout"), self.valid_form())

        order = Order.objects.get()
        self.assertRedirects(response, order.get_absolute_url())

        self.assertEqual(order.payment_method, "cash")
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(order.order_status, "new")
        self.assertEqual(order.subtotal, Decimal("900.00"))
        self.assertEqual(order.shipping_cost, Decimal("60.00"))
        self.assertEqual(order.total_price, Decimal("960.00"))

        item = order.items.get()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.unit_price, Decimal("450.00"))
        # Snapshot, not a live join.
        self.assertEqual(item.product_name, self.product.name)

        variant = ProductVariant.objects.get(product=self.product, size="M")
        self.assertEqual(variant.stock, 3)

        # The bag is emptied so a refresh cannot duplicate the order.
        self.assertEqual(self.client.session.get("cart"), {})

    def test_invalid_phone_is_rejected(self):
        self.fill_cart()
        response = self.client.post(
            reverse("checkout"), self.valid_form(phone="12345")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertIn("phone", response.context["form"].errors)

    def test_short_address_is_rejected(self):
        self.fill_cart()
        response = self.client.post(reverse("checkout"), self.valid_form(address="x"))

        self.assertFalse(Order.objects.exists())
        self.assertIn("address", response.context["form"].errors)

    def test_unknown_governorate_is_rejected(self):
        self.fill_cart()
        response = self.client.post(
            reverse("checkout"), self.valid_form(governorate="Atlantis")
        )

        self.assertFalse(Order.objects.exists())
        self.assertIn("governorate", response.context["form"].errors)

    def test_online_payment_requires_an_email(self):
        self.fill_cart()
        response = self.client.post(
            reverse("checkout"), self.valid_form(payment="online", email="")
        )

        self.assertFalse(Order.objects.exists())
        self.assertIn("email", response.context["form"].errors)

    def test_phone_is_normalised(self):
        self.fill_cart()
        self.client.post(reverse("checkout"), self.valid_form(phone="+20 101 234 5678"))

        self.assertEqual(Order.objects.get().phone, "01012345678")

    def test_checkout_stops_if_stock_dropped_before_submit(self):
        """The customer must not be charged for an order they never reviewed."""
        self.fill_cart(quantity=2)
        # Someone else buys most of the remaining stock in the meantime.
        ProductVariant.objects.filter(product=self.product, size="M").update(stock=1)

        response = self.client.post(reverse("checkout"), self.valid_form())

        self.assertRedirects(response, reverse("cart"))
        self.assertFalse(Order.objects.exists())

    def test_checkout_stops_if_an_item_sold_out_entirely(self):
        self.fill_cart(quantity=1)
        ProductVariant.objects.filter(product=self.product, size="M").update(stock=0)

        response = self.client.post(reverse("checkout"), self.valid_form())

        self.assertRedirects(response, reverse("store"))
        self.assertFalse(Order.objects.exists())

    def test_second_attempt_succeeds_at_the_adjusted_quantity(self):
        """After reviewing the trimmed bag, checkout goes through."""
        self.fill_cart(quantity=2)
        ProductVariant.objects.filter(product=self.product, size="M").update(stock=1)

        self.client.post(reverse("checkout"), self.valid_form())  # bounced back
        self.client.get(reverse("cart"))  # customer reviews the bag

        response = self.client.post(reverse("checkout"), self.valid_form())

        order = Order.objects.get()
        self.assertRedirects(response, order.get_absolute_url())
        self.assertEqual(order.items.get().quantity, 1)
        self.assertEqual(order.subtotal, Decimal("450.00"))

    @override_settings(FAWATERK_HASH_KEY="")
    def test_online_payment_is_refused_when_the_gateway_is_unconfigured(self):
        """No silent HttpResponse: the order is failed and stock returned."""
        self.fill_cart(quantity=1)
        response = self.client.post(
            reverse("checkout"), self.valid_form(payment="online")
        )

        self.assertRedirects(response, reverse("checkout"))
        order = Order.objects.get()
        self.assertEqual(order.payment_status, "failed")

        variant = ProductVariant.objects.get(product=self.product, size="M")
        self.assertEqual(variant.stock, 5)  # reservation released

    @patch("store.services.fawaterk.requests.post")
    def test_online_payment_redirects_to_the_invoice(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "status": "success",
            "data": {
                "url": "https://staging.fawaterk.com/link/XYZ",
                "invoiceKey": "KEY9",
                "invoiceId": 777,
            },
        }

        self.fill_cart(quantity=1)
        response = self.client.post(
            reverse("checkout"), self.valid_form(payment="online")
        )

        self.assertRedirects(
            response,
            "https://staging.fawaterk.com/link/XYZ",
            fetch_redirect_response=False,
        )

        order = Order.objects.get()
        self.assertEqual(order.fawaterk_invoice_id, "777")
        self.assertEqual(order.payment_status, "pending")
        # Bag cleared, so a refresh cannot create a second order.
        self.assertEqual(self.client.session.get("cart"), {})

    @patch(
        "store.services.fawaterk.requests.post",
        side_effect=fawaterk.requests.Timeout("slow"),
    )
    def test_gateway_timeout_leaves_a_recoverable_order(self, mock_post):
        self.fill_cart(quantity=1)
        response = self.client.post(
            reverse("checkout"), self.valid_form(payment="online")
        )

        self.assertRedirects(response, reverse("checkout"))
        order = Order.objects.get()
        self.assertEqual(order.payment_status, "failed")
        self.assertIn("timed out", order.payment_error.lower())

    def test_order_number_is_unique_per_order(self):
        for _ in range(3):
            self.fill_cart(quantity=1)
            self.client.post(reverse("checkout"), self.valid_form())

        numbers = set(Order.objects.values_list("order_number", flat=True))
        self.assertEqual(len(numbers), 3)


# ---------------------------------------------------------------------------
# Order access control
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class OrderAccessTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            full_name="Ahmed Hassan",
            phone="01012345678",
            governorate="Cairo",
            city="Nasr City",
            address="12 Abbas El Akkad Street",
            payment_method="cash",
            subtotal=Decimal("450.00"),
            total_price=Decimal("510.00"),
        )

    def test_correct_token_shows_the_order(self):
        response = self.client.get(self.order.get_absolute_url())
        self.assertContains(response, self.order.order_number)

    def test_wrong_token_is_not_found(self):
        url = reverse(
            "order_detail",
            kwargs={"order_number": self.order.order_number, "token": "guessed"},
        )
        self.assertEqual(self.client.get(url).status_code, 404)


# ---------------------------------------------------------------------------
# Fawaterk client
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class FawaterkClientTests(TestCase):
    def setUp(self):
        fawaterk.reset_token_cache()
        self.product = make_product()
        self.order = Order.objects.create(
            full_name="Ahmed Hassan Ali",
            phone="01012345678",
            email="ahmed@example.com",
            governorate="Cairo",
            city="Nasr City",
            address="12 Abbas El Akkad Street",
            payment_method="online",
            subtotal=Decimal("450.00"),
            shipping_cost=Decimal("60.00"),
            total_price=Decimal("510.00"),
            currency="EGP",
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            size="M",
            quantity=1,
            unit_price=Decimal("450.00"),
        )

    def test_split_name_always_returns_a_last_name(self):
        self.assertEqual(fawaterk.split_name("Ahmed"), ("Ahmed", "Ahmed"))
        self.assertEqual(
            fawaterk.split_name("Ahmed Hassan Ali"), ("Ahmed", "Hassan Ali")
        )
        self.assertEqual(fawaterk.split_name(""), ("Customer", "Order"))

    @patch("store.services.fawaterk.requests.post")
    def test_create_invoice_sends_the_expected_payload(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "status": "success",
            "data": {
                "url": "https://staging.fawaterk.com/link/ABC",
                "invoiceKey": "KEY123",
                "invoiceId": 999,
            },
        }

        result = fawaterk.create_invoice(
            self.order,
            success_url="https://site.test/ok/",
            fail_url="https://site.test/fail/",
            pending_url="https://site.test/pending/",
            webhook_url="https://site.test/hook/",
        )

        self.assertEqual(result["url"], "https://staging.fawaterk.com/link/ABC")
        self.assertEqual(result["invoice_id"], "999")

        # Exactly one HTTP call: no separate OAuth token request any more.
        self.assertEqual(mock_post.call_count, 1)
        call = mock_post.call_args
        self.assertIn("createInvoiceLink", call.args[0])

        payload = call.kwargs["json"]
        # cartTotal is the product subtotal only (confirmed by a live request
        # against production -- see the comment in fawaterk.create_invoice).
        # shipping travels separately and is asserted unchanged below.
        self.assertEqual(payload["cartTotal"], "450.00")
        self.assertEqual(payload["currency"], "EGP")
        self.assertEqual(payload["shipping"], "60.00")
        self.assertEqual(payload["customer"]["first_name"], "Ahmed")
        # Our own reference travels with the invoice and comes back on the hook.
        self.assertEqual(payload["payLoad"]["order_number"], self.order.order_number)
        self.assertEqual(
            payload["redirectionUrls"]["webhookUrl"], "https://site.test/hook/"
        )

        # Confirmed by a live test against the real account: createInvoiceLink
        # accepts the static hash/API key, not an OAuth-derived token.
        headers = call.kwargs["headers"]
        self.assertEqual(headers["Authorization"], f"Bearer {HASH_KEY}")

    @patch("store.services.fawaterk.requests.post")
    def test_create_invoice_raises_on_a_gateway_error(self, mock_post):
        mock_post.return_value.status_code = 422
        mock_post.return_value.json.return_value = {
            "status": "error",
            "message": "cartTotal mismatch",
        }

        with self.assertRaises(fawaterk.FawaterkError) as ctx:
            fawaterk.create_invoice(
                self.order,
                success_url="https://site.test/ok/",
                fail_url="https://site.test/fail/",
                pending_url="https://site.test/pending/",
            )
        self.assertIn("cartTotal mismatch", str(ctx.exception))

    @patch("store.services.fawaterk.requests.post", side_effect=Exception("boom"))
    def test_unexpected_errors_are_not_swallowed_silently(self, mock_post):
        with self.assertRaises(Exception):
            fawaterk.create_invoice(
                self.order,
                success_url="https://site.test/ok/",
                fail_url="https://site.test/fail/",
                pending_url="https://site.test/pending/",
            )

    def test_webhook_hash_verification(self):
        payload = valid_paid_payload(self.order)
        self.assertTrue(fawaterk.verify_invoice_hash(payload))

        payload["hashKey"] = "0" * 64
        self.assertFalse(fawaterk.verify_invoice_hash(payload))

    def test_webhook_hash_rejects_a_tampered_amount_reference(self):
        payload = valid_paid_payload(self.order)
        payload["invoice_id"] = "99999"  # changed after signing
        self.assertFalse(fawaterk.verify_invoice_hash(payload))

    def test_expired_hash_uses_its_own_signed_string(self):
        query = "referenceId=778586510&PaymentMethod=Fawry"
        signature = hmac.new(
            HASH_KEY.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        payload = {
            "hashKey": signature,
            "referenceId": "778586510",
            "paymentMethod": "Fawry",
            "status": "EXPIRED",
        }
        self.assertTrue(fawaterk.verify_expired_hash(payload))


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class WebhookTests(TestCase):
    def setUp(self):
        self.product = make_product(stock=5)
        self.order = Order.objects.create(
            full_name="Ahmed Hassan",
            phone="01012345678",
            email="ahmed@example.com",
            governorate="Cairo",
            city="Nasr City",
            address="12 Abbas El Akkad Street",
            payment_method="online",
            subtotal=Decimal("450.00"),
            shipping_cost=Decimal("60.00"),
            total_price=Decimal("510.00"),
            fawaterk_invoice_id="12345",
        )
        self.variant = ProductVariant.objects.get(product=self.product, size="M")
        self.variant.stock = 3  # 2 reserved by this order
        self.variant.save()
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            size="M",
            quantity=2,
            unit_price=Decimal("450.00"),
        )
        self.url = reverse("fawaterk_webhook")

    def send(self, payload):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json"
        )

    def test_valid_paid_webhook_marks_the_order_paid(self):
        response = self.send(valid_paid_payload(self.order))
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self.order.order_status, "confirmed")
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(self.order.fawaterk_reference, "9988776655")

        event = WebhookEvent.objects.get()
        self.assertTrue(event.signature_valid)
        self.assertTrue(event.processed)
        self.assertEqual(event.order, self.order)

    def test_repeated_webhook_is_idempotent(self):
        payload = valid_paid_payload(self.order)
        self.send(payload)
        self.order.refresh_from_db()
        first_paid_at = self.order.paid_at

        self.send(payload)
        self.order.refresh_from_db()

        self.assertEqual(self.order.paid_at, first_paid_at)
        self.assertEqual(WebhookEvent.objects.count(), 2)
        self.assertEqual(
            WebhookEvent.objects.filter(message="Already paid, ignored").count(), 1
        )

    def test_bad_signature_is_rejected_and_logged(self):
        payload = valid_paid_payload(self.order)
        payload["hashKey"] = "deadbeef"

        response = self.send(payload)
        self.assertEqual(response.status_code, 403)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "pending")

        event = WebhookEvent.objects.get()
        self.assertFalse(event.signature_valid)
        self.assertFalse(event.processed)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_webhook_rejects_get(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_unmatched_order_returns_200_so_the_gateway_stops_retrying(self):
        payload = valid_paid_payload(self.order)
        payload["pay_load"] = {"order_number": "HA-000000-ZZZZZZ"}
        payload["invoice_id"] = "does-not-exist"
        # Re-sign for the changed invoice id.
        query = (
            f"InvoiceId=does-not-exist&InvoiceKey={payload['invoice_key']}"
            f"&PaymentMethod={payload['payment_method']}"
        )
        payload["hashKey"] = hmac.new(
            HASH_KEY.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

        response = self.send(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")

    def test_failed_webhook_marks_failed_and_returns_stock(self):
        query = "InvoiceId=12345&InvoiceKey=INVKEY123&PaymentMethod=Card"
        payload = {
            "hashKey": hmac.new(
                HASH_KEY.encode(), query.encode(), hashlib.sha256
            ).hexdigest(),
            "invoice_id": "12345",
            "invoice_key": "INVKEY123",
            "payment_method": "Card",
            "amount": 510,
            "paidCurrency": "EGP",
            "errorMessage": "3D Secure authentication failed",
            "pay_load": {"order_number": self.order.order_number},
        }

        response = self.send(payload)
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "failed")
        self.assertIn("3D Secure", self.order.payment_error)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 5)  # the 2 reserved came back

    def test_a_late_failure_cannot_unpay_a_paid_order(self):
        self.send(valid_paid_payload(self.order))

        query = "InvoiceId=12345&InvoiceKey=INVKEY123&PaymentMethod=Card"
        self.send(
            {
                "hashKey": hmac.new(
                    HASH_KEY.encode(), query.encode(), hashlib.sha256
                ).hexdigest(),
                "invoice_id": "12345",
                "invoice_key": "INVKEY123",
                "payment_method": "Card",
                "errorMessage": "Declined",
                "pay_load": {"order_number": self.order.order_number},
            }
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_form_encoded_webhook_is_accepted(self):
        """Fawaterk only sends JSON when the URL contains _json."""
        payload = valid_paid_payload(self.order)
        payload["pay_load"] = json.dumps({"order_number": self.order.order_number})

        response = self.client.post(self.url, data=payload)
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_legacy_webhook_url_still_works(self):
        response = self.client.post(
            reverse("paid_webhook_legacy"),
            data=json.dumps(valid_paid_payload(self.order)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")


# ---------------------------------------------------------------------------
# Payment return pages
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class PaymentReturnTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            full_name="Ahmed Hassan",
            phone="01012345678",
            governorate="Cairo",
            city="Nasr City",
            address="12 Abbas El Akkad Street",
            payment_method="online",
            subtotal=Decimal("450.00"),
            total_price=Decimal("510.00"),
            fawaterk_invoice_id="12345",
        )

    def url(self, name):
        return reverse(
            name,
            kwargs={
                "order_number": self.order.order_number,
                "token": self.order.access_token,
            },
        )

    @patch("store.services.fawaterk.get_invoice_data")
    def test_success_page_verifies_with_the_gateway(self, mock_lookup):
        mock_lookup.return_value = {"paid": 1, "payment_method": "Card"}

        response = self.client.get(self.url("payment_success"))
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        mock_lookup.assert_called_once_with("12345")

    @patch("store.services.fawaterk.get_invoice_data")
    def test_success_page_does_not_trust_the_redirect_alone(self, mock_lookup):
        """Hitting the success URL directly must not mark an order paid."""
        mock_lookup.return_value = {"paid": 0}

        response = self.client.get(self.url("payment_success"))

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "pending")
        self.assertEqual(response.context["outcome"], "pending")

    @patch("store.services.fawaterk.get_invoice_data", return_value=None)
    def test_unreachable_gateway_leaves_the_order_pending(self, mock_lookup):
        response = self.client.get(self.url("payment_success"))

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "pending")
        self.assertEqual(response.context["outcome"], "pending")

    def test_fail_page_marks_the_order_failed(self):
        response = self.client.get(self.url("payment_failed"))
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "failed")

    def test_fail_page_cannot_unpay_a_paid_order(self):
        self.order.mark_paid(invoice_id="12345")

        self.client.get(self.url("payment_failed"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")


# ---------------------------------------------------------------------------
# TikTok helpers
# ---------------------------------------------------------------------------

class TikTokTests(TestCase):
    def test_email_is_lowercased_before_hashing(self):
        self.assertEqual(
            tiktok.hash_email("  Ahmed@Example.COM "),
            hashlib.sha256(b"ahmed@example.com").hexdigest(),
        )

    def test_invalid_email_is_omitted(self):
        self.assertEqual(tiktok.hash_email("not-an-email"), "")
        self.assertEqual(tiktok.hash_email(""), "")

    def test_local_phone_is_converted_to_e164(self):
        expected = hashlib.sha256(b"+201012345678").hexdigest()
        self.assertEqual(tiktok.hash_phone("01012345678"), expected)
        self.assertEqual(tiktok.hash_phone("+20 101 234 5678"), expected)
        self.assertEqual(tiktok.hash_phone("0020 1012345678"), expected)

    def test_purchase_event_name_is_tiktoks_own(self):
        """TikTok's purchase event is CompletePayment, not Meta's 'Purchase'."""
        self.assertEqual(tiktok.PURCHASE, "CompletePayment")

    @override_settings(TIKTOK_PIXEL_ID="", TIKTOK_ACCESS_TOKEN="")
    def test_events_are_skipped_when_unconfigured(self):
        self.assertFalse(tiktok.send_event("ViewContent"))

    @override_settings(TIKTOK_PIXEL_ID="PIX", TIKTOK_ACCESS_TOKEN="TOK")
    @patch("store.services.tiktok.requests.post")
    def test_purchase_is_sent_only_once_per_order(self, mock_post):
        mock_post.return_value.json.return_value = {"code": 0}

        order = Order.objects.create(
            full_name="Ahmed Hassan",
            phone="01012345678",
            governorate="Cairo",
            city="Nasr City",
            address="12 Abbas El Akkad Street",
            payment_method="online",
            total_price=Decimal("510.00"),
        )

        self.assertTrue(tiktok.send_purchase(order))
        self.assertFalse(tiktok.send_purchase(order))
        self.assertEqual(mock_post.call_count, 1)

    @override_settings(TIKTOK_PIXEL_ID="PIX", TIKTOK_ACCESS_TOKEN="TOK")
    @patch("store.services.tiktok.requests.post", side_effect=Exception("network"))
    def test_a_tracking_failure_never_raises(self, mock_post):
        self.assertFalse(tiktok.send_event("ViewContent"))


# ---------------------------------------------------------------------------
# Model behaviour and template filters
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class ModelTests(TestCase):
    def test_deleting_a_product_preserves_order_history(self):
        product = make_product()
        order = Order.objects.create(
            full_name="Ahmed Hassan",
            phone="01012345678",
            governorate="Cairo",
            city="Nasr City",
            address="12 Abbas El Akkad Street",
            payment_method="cash",
            total_price=Decimal("450.00"),
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            size="M",
            quantity=1,
            unit_price=Decimal("450.00"),
        )

        product.delete()

        item = order.items.get()
        self.assertIsNone(item.product)
        self.assertEqual(item.product_name, "Product tee")
        self.assertEqual(item.unit_price, Decimal("450.00"))

    def test_duplicate_size_for_a_product_is_rejected(self):
        product = make_product(sizes=("M",))
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            ProductVariant.objects.create(product=product, size="M", stock=1)

    def test_image_reference_accepts_a_path_or_a_url(self):
        product = make_product()
        self.assertTrue(product.front_image_url.endswith("images/store/front.png"))

        product.front_image = "https://cdn.example.com/tee.png"
        self.assertEqual(product.front_image_url, "https://cdn.example.com/tee.png")

    def test_sale_percentage(self):
        product = make_product(price="400.00")
        product.compare_at_price = Decimal("500.00")
        self.assertTrue(product.is_on_sale)
        self.assertEqual(product.discount_percent, 20)

    def test_money_filter_formats_with_the_currency(self):
        from .templatetags.store_extras import money

        self.assertEqual(money(Decimal("1250.00")), "1,250 EGP")
        self.assertEqual(money(Decimal("1250.50")), "1,250.50 EGP")
        self.assertEqual(money(None), "0 EGP")


# ---------------------------------------------------------------------------
# Page smoke tests
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class PageTests(TestCase):
    def setUp(self):
        self.product = make_product()

    def test_shop_page_renders(self):
        response = self.client.get(reverse("store"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.name)

    def test_product_page_renders(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add to bag")

    def test_unknown_product_is_404(self):
        response = self.client.get(
            reverse("product_detail", kwargs={"slug": "nope"})
        )
        self.assertEqual(response.status_code, 404)

    def test_inactive_product_is_404(self):
        self.product.is_active = False
        self.product.save(update_fields=["is_active"])
        self.assertEqual(
            self.client.get(self.product.get_absolute_url()).status_code, 404
        )

    def test_empty_cart_page_renders(self):
        response = self.client.get(reverse("cart"))
        self.assertContains(response, "Your bag is empty")

    @override_settings(TIKTOK_PIXEL_ID="TESTPIXEL")
    def test_pixel_is_rendered_when_configured(self):
        response = self.client.get(reverse("store"))
        self.assertContains(response, "TESTPIXEL")
        self.assertContains(response, "analytics.tiktok.com")

    def test_pixel_is_absent_when_unconfigured(self):
        response = self.client.get(reverse("store"))
        self.assertNotContains(response, "analytics.tiktok.com")

    def test_access_token_is_never_rendered(self):
        """The Events API token must not leak into any page."""
        with override_settings(
            TIKTOK_PIXEL_ID="PIX", TIKTOK_ACCESS_TOKEN="SUPER-SECRET-TOKEN"
        ):
            response = self.client.get(reverse("store"))
        self.assertNotContains(response, "SUPER-SECRET-TOKEN")

    def test_marketing_pages_still_render(self):
        for name in ["index", "about", "pricing", "book", "protein", "calories",
                     "proteinen", "caloriesen", "second"]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


# ---------------------------------------------------------------------------
# Full journey
# ---------------------------------------------------------------------------

@override_settings(**{**TEST_SETTINGS, "TIKTOK_PIXEL_ID": "PIX",
                      "TIKTOK_ACCESS_TOKEN": "TOK"})
class PurchaseJourneyTests(TestCase):
    """Browse, add, pay by card, get the webhook, land on the receipt.

    This is the path real money takes, so it is exercised as one continuous
    scenario rather than only in pieces.
    """

    def setUp(self):
        self.product = make_product(slug="tee", price="450.00", stock=4)

    @patch("requests.post")
    def test_card_purchase_from_catalogue_to_receipt(self, mock_post):
        # store.services.fawaterk and store.services.tiktok both do
        # `import requests`, so they share one module object and one `post`.
        # A single mock dispatching on the URL keeps both paths honest.
        def dispatch(url, **kwargs):
            response = MagicMock()
            response.status_code = 200
            if "fawaterk" in url:
                response.json.return_value = {
                    "status": "success",
                    "data": {
                        "url": "https://staging.fawaterk.com/link/JOURNEY",
                        "invoiceKey": "INVKEY123",
                        "invoiceId": 12345,
                    },
                }
            else:
                response.json.return_value = {"code": 0}
            return response

        mock_post.side_effect = dispatch
        tiktok_calls = mock_post.call_args_list

        # 1. Browse and add two shirts.
        self.assertEqual(self.client.get(reverse("store")).status_code, 200)
        self.assertEqual(
            self.client.get(self.product.get_absolute_url()).status_code, 200
        )
        self.client.post(
            reverse("cart_add"),
            {"slug": "tee", "size": "M", "quantity": 2},
        )

        # 2. Checkout, paying by card.
        response = self.client.post(
            reverse("checkout"),
            {
                "full_name": "Ahmed Hassan",
                "phone": "01012345678",
                "email": "ahmed@example.com",
                "governorate": "Cairo",
                "city": "Nasr City",
                "address": "12 Abbas El Akkad Street, floor 3, apartment 7",
                "notes": "",
                "payment": "online",
            },
        )

        order = Order.objects.get()
        self.assertRedirects(
            response,
            "https://staging.fawaterk.com/link/JOURNEY",
            fetch_redirect_response=False,
        )
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(order.total_price, Decimal("960.00"))
        self.assertEqual(order.fawaterk_invoice_id, "12345")

        # Stock is held while the customer is on the gateway.
        variant = ProductVariant.objects.get(product=self.product, size="M")
        self.assertEqual(variant.stock, 2)

        # 3. Fawaterk confirms payment.
        response = self.client.post(
            reverse("fawaterk_webhook"),
            data=json.dumps(valid_paid_payload(order)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.order_status, "confirmed")
        self.assertTrue(order.tiktok_purchase_sent)

        purchase_calls = _tiktok_purchases(tiktok_calls)
        self.assertEqual(len(purchase_calls), 1)

        # The reported value must match the order, not the cart.
        sent = purchase_calls[0]
        self.assertEqual(sent["properties"]["value"], 960.0)
        self.assertEqual(sent["event_id"], order.tiktok_event_id)
        # Identifiers are hashed, never sent in the clear.
        self.assertNotIn("ahmed@example.com", json.dumps(sent))

        # 4. The customer lands on the receipt.
        response = self.client.get(
            reverse(
                "payment_success",
                kwargs={
                    "order_number": order.order_number,
                    "token": order.access_token,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment received")
        self.assertContains(response, order.order_number)
        # Pixel event id matches the server event, so TikTok deduplicates.
        self.assertContains(response, order.tiktok_event_id)

        # 5. A duplicate webhook changes nothing and does not re-report.
        self.client.post(
            reverse("fawaterk_webhook"),
            data=json.dumps(valid_paid_payload(order)),
            content_type="application/json",
        )
        self.assertEqual(len(_tiktok_purchases(tiktok_calls)), 1)

    @patch("store.services.fawaterk.requests.post")
    def test_abandoned_payment_returns_stock(self, mock_gateway):
        mock_gateway.return_value.status_code = 200
        mock_gateway.return_value.json.return_value = {
            "status": "success",
            "data": {"url": "https://x/y", "invoiceKey": "K", "invoiceId": 12345},
        }

        self.client.post(reverse("cart_add"), {"slug": "tee", "size": "M", "quantity": 2})
        self.client.post(
            reverse("checkout"),
            {
                "full_name": "Ahmed Hassan",
                "phone": "01012345678",
                "email": "ahmed@example.com",
                "governorate": "Cairo",
                "city": "Nasr City",
                "address": "12 Abbas El Akkad Street, floor 3, apartment 7",
                "payment": "online",
            },
        )
        order = Order.objects.get()

        # Customer backs out; Fawaterk sends them to the fail URL.
        self.client.get(
            reverse(
                "payment_failed",
                kwargs={
                    "order_number": order.order_number,
                    "token": order.access_token,
                },
            )
        )

        order.refresh_from_db()
        self.assertEqual(order.payment_status, "failed")

        variant = ProductVariant.objects.get(product=self.product, size="M")
        self.assertEqual(variant.stock, 4)  # back on the shelf


# ---------------------------------------------------------------------------
# Fawaterk authentication
# ---------------------------------------------------------------------------
# fetch_access_token() / reset_token_cache() are kept working and available
# (some other Fawaterk product may need them later), but nothing in this
# module calls them any more. createInvoiceLink and getInvoiceData always use
# the static hash/API key -- confirmed by a live request against the
# project's own production account: the static key returned a real invoice
# (HTTP 200), the OAuth-derived token was rejected with "Invalid Token or
# inactive vendor" (HTTP 400) even though the token endpoint itself worked.

@override_settings(**TEST_SETTINGS)
class FawaterkAuthTests(TestCase):
    def setUp(self):
        fawaterk.reset_token_cache()

    def _token_response(self, expires_in=3600, token="tok-abc"):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
        }
        return response

    # -- fetch_access_token() itself: kept working, kept tested -------------

    @patch("store.services.fawaterk.requests.post")
    def test_token_is_requested_with_the_client_credentials_grant(self, mock_post):
        mock_post.return_value = self._token_response()

        token = fawaterk.fetch_access_token()

        self.assertEqual(token, "tok-abc")
        self.assertEqual(mock_post.call_args.args[0],
                         "https://app.fawaterk.com/oauth/token")
        sent = mock_post.call_args.kwargs["data"]
        self.assertEqual(sent["grant_type"], "client_credentials")
        self.assertEqual(sent["client_id"], "test-client-id")
        self.assertEqual(sent["client_secret"], "test-client-secret")

    @patch("store.services.fawaterk.requests.post")
    def test_token_is_cached_between_calls(self, mock_post):
        mock_post.return_value = self._token_response()

        fawaterk.fetch_access_token()
        fawaterk.fetch_access_token()
        fawaterk.fetch_access_token()

        self.assertEqual(mock_post.call_count, 1)

    @patch("store.services.fawaterk.requests.post")
    def test_expiring_token_is_refetched(self, mock_post):
        # expires_in below the 60s safety margin means it is never cached.
        mock_post.return_value = self._token_response(expires_in=10)

        fawaterk.fetch_access_token()
        fawaterk.fetch_access_token()

        self.assertEqual(mock_post.call_count, 2)

    @patch("store.services.fawaterk.requests.post")
    def test_token_endpoint_without_a_token_is_handled(self, mock_post):
        bad = MagicMock()
        bad.status_code = 401
        bad.json.return_value = {"error": "invalid_client"}
        mock_post.return_value = bad

        self.assertEqual(fawaterk.fetch_access_token(), "")

    # -- _bearer_token(): always the static key, never touches fetch_access_token --

    @patch("store.services.fawaterk.requests.post")
    def test_bearer_token_is_always_the_static_hash_key(self, mock_post):
        """_bearer_token() must not make a network call at all any more."""
        self.assertEqual(fawaterk._bearer_token(), HASH_KEY)
        mock_post.assert_not_called()

    # -- create_invoice(): single call, static key, no OAuth, no retry ------

    @patch("store.services.fawaterk.requests.post")
    def test_create_invoice_never_calls_the_oauth_token_endpoint(self, mock_post):
        product = make_product()
        order = Order.objects.create(
            full_name="Ahmed Hassan", phone="01012345678",
            governorate="Cairo", city="Nasr City",
            address="12 Abbas El Akkad Street", payment_method="online",
            subtotal=Decimal("450.00"), total_price=Decimal("450.00"),
        )
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name,
            size="M", quantity=1, unit_price=Decimal("450.00"),
        )

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "status": "success",
            "data": {"url": "https://x/y", "invoiceKey": "K", "invoiceId": 5},
        }

        result = fawaterk.create_invoice(
            order,
            success_url="https://site.test/ok/",
            fail_url="https://site.test/fail/",
            pending_url="https://site.test/pending/",
        )

        self.assertEqual(result["invoice_id"], "5")
        # Exactly one HTTP call, and it is not the token endpoint.
        self.assertEqual(mock_post.call_count, 1)
        called_url = mock_post.call_args.args[0]
        self.assertNotIn("oauth/token", called_url)
        self.assertIn("createInvoiceLink", called_url)
        self.assertEqual(
            mock_post.call_args.kwargs["headers"]["Authorization"],
            f"Bearer {HASH_KEY}",
        )

    @patch("store.services.fawaterk.requests.post")
    def test_a_rejected_key_is_not_retried(self, mock_post):
        """No refresh-and-retry left: one rejection is one failure."""
        product = make_product()
        order = Order.objects.create(
            full_name="Ahmed Hassan", phone="01012345678",
            governorate="Cairo", city="Nasr City",
            address="12 Abbas El Akkad Street", payment_method="online",
            subtotal=Decimal("450.00"), total_price=Decimal("450.00"),
        )
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name,
            size="M", quantity=1, unit_price=Decimal("450.00"),
        )
        mock_post.return_value.status_code = 400
        mock_post.return_value.json.return_value = {
            "status": "error",
            "message": {"token": ["Invalid Token or inactive vendor."]},
        }

        with self.assertRaises(fawaterk.FawaterkError):
            fawaterk.create_invoice(
                order,
                success_url="https://site.test/ok/",
                fail_url="https://site.test/fail/",
                pending_url="https://site.test/pending/",
            )

        self.assertEqual(mock_post.call_count, 1)

    # -- get_invoice_data(): same corrected auth path ------------------------

    @patch("store.services.fawaterk.requests.get")
    def test_get_invoice_data_uses_the_static_hash_key(self, mock_get):
        mock_get.return_value.json.return_value = {
            "status": "success",
            "data": {"paid": 1},
        }

        data = fawaterk.get_invoice_data("12345")

        self.assertEqual(data, {"paid": 1})
        self.assertEqual(
            mock_get.call_args.kwargs["headers"]["Authorization"],
            f"Bearer {HASH_KEY}",
        )

    # -- webhook HMAC verification: untouched by this fix --------------------

    def test_webhook_secret_is_the_hash_key_not_the_oauth_token(self):
        """Signatures must be verifiable with a value that does not rotate."""
        order = Order.objects.create(
            full_name="A B", phone="01012345678", governorate="Cairo",
            city="Nasr City", address="12 Street", payment_method="online",
            total_price=Decimal("100.00"),
        )
        self.assertTrue(fawaterk.verify_invoice_hash(valid_paid_payload(order)))

    # -- debug logging: secrets stay masked -----------------------------------

    @patch("store.services.fawaterk.requests.post")
    def test_debug_logging_masks_the_authorization_header(self, mock_post):
        product = make_product()
        order = Order.objects.create(
            full_name="Ahmed Hassan", phone="01012345678",
            governorate="Cairo", city="Nasr City",
            address="12 Abbas El Akkad Street", payment_method="online",
            subtotal=Decimal("450.00"), total_price=Decimal("450.00"),
        )
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name,
            size="M", quantity=1, unit_price=Decimal("450.00"),
        )
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "status": "success",
            "data": {"url": "https://x/y", "invoiceKey": "K", "invoiceId": 5},
        }

        with override_settings(FAWATERK_DEBUG_LOGGING=True):
            with self.assertLogs("store.services.fawaterk", level="INFO") as logs:
                fawaterk.create_invoice(
                    order,
                    success_url="https://site.test/ok/",
                    fail_url="https://site.test/fail/",
                    pending_url="https://site.test/pending/",
                )

        logged_text = "\n".join(logs.output)
        self.assertNotIn(HASH_KEY, logged_text)
        self.assertIn("request", logged_text)
        self.assertIn("response", logged_text)


# ---------------------------------------------------------------------------
# Order notification email
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class OrderEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.product = make_product(stock=5)
        self.order = Order.objects.create(
            full_name="Ahmed Hassan",
            phone="01012345678",
            email="ahmed@example.com",
            governorate="Dakahlia",
            city="Mansoura",
            address="12 El Gomhouria Street, floor 2, apt 5",
            notes="Call before arriving",
            payment_method="online",
            subtotal=Decimal("900.00"),
            shipping_cost=Decimal("60.00"),
            total_price=Decimal("960.00"),
            fawaterk_invoice_id="12345",
            fawaterk_invoice_key="INVKEY123",
        )
        self.variant = ProductVariant.objects.get(product=self.product, size="M")
        OrderItem.objects.create(
            order=self.order, product=self.product, variant=self.variant,
            product_name=self.product.name, product_image=self.product.front_image,
            size="M", quantity=2, unit_price=Decimal("450.00"),
        )

    def test_email_contains_every_requested_field(self):
        self.order.mark_paid(invoice_id="12345", method="Card", reference="998877")
        notifications.send_order_notification(self.order, reason="paid")

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["owner@example.com"])

        text = message.body
        html = message.alternatives[0][0]

        for body in (text, html):
            self.assertIn(self.order.order_number, body)      # order number
            self.assertIn("Ahmed Hassan", body)               # customer name
            self.assertIn("ahmed@example.com", body)          # customer email
            self.assertIn("01012345678", body)                # customer phone
            self.assertIn("El Gomhouria", body)               # shipping address
            self.assertIn("Mansoura", body)                   # city
            self.assertIn("Dakahlia", body)                   # governorate
            self.assertIn("Product tee", body)                # product
            self.assertIn("450", body)                        # unit price
            self.assertIn("60", body)                         # shipping cost
            self.assertIn("960", body)                        # total
            self.assertIn("12345", body)                      # invoice reference
            self.assertIn("998877", body)                     # transaction ref
            self.assertIn("PAID", body.upper())               # payment status

        # Quantity and size appear in both renderings.
        self.assertIn("Size     : M", text)
        self.assertIn("Quantity : 2", text)
        # Subject carries the essentials for a phone notification.
        self.assertIn("PAID", message.subject)
        self.assertIn(self.order.order_number, message.subject)
        # Replying reaches the customer.
        self.assertEqual(message.reply_to, ["ahmed@example.com"])

    def test_email_is_sent_once_when_the_webhook_confirms_payment(self):
        response = self.client.post(
            reverse("fawaterk_webhook"),
            data=json.dumps(valid_paid_payload(self.order)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("PAID", mail.outbox[0].subject)

    def test_duplicate_webhooks_do_not_send_duplicate_emails(self):
        payload = valid_paid_payload(self.order)
        self.client.post(reverse("fawaterk_webhook"), data=json.dumps(payload),
                         content_type="application/json")
        self.client.post(reverse("fawaterk_webhook"), data=json.dumps(payload),
                         content_type="application/json")
        self.client.post(reverse("fawaterk_webhook"), data=json.dumps(payload),
                         content_type="application/json")

        self.assertEqual(len(mail.outbox), 1)

    @patch("store.services.fawaterk.get_invoice_data")
    def test_email_is_sent_when_the_success_page_verifies_payment(self, mock_lookup):
        mock_lookup.return_value = {"paid": 1, "payment_method": "Card"}

        self.client.get(
            reverse("payment_success", kwargs={
                "order_number": self.order.order_number,
                "token": self.order.access_token,
            })
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_cash_order_also_notifies_the_owner(self):
        self.client.post(reverse("cart_add"),
                         {"slug": self.product.slug, "size": "L", "quantity": 1})
        self.client.post(reverse("checkout"), {
            "full_name": "Mona Samir", "phone": "01112223334",
            "email": "", "governorate": "Giza", "city": "Dokki",
            "address": "5 Tahrir Street, building 2, apartment 9",
            "notes": "", "payment": "cash",
        })

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("COD", mail.outbox[0].subject)
        self.assertIn("Mona Samir", mail.outbox[0].body)

    @override_settings(NOTIFY_ON_COD_ORDER=False)
    def test_cod_notification_can_be_switched_off(self):
        self.client.post(reverse("cart_add"),
                         {"slug": self.product.slug, "size": "L", "quantity": 1})
        self.client.post(reverse("checkout"), {
            "full_name": "Mona Samir", "phone": "01112223334",
            "email": "", "governorate": "Giza", "city": "Dokki",
            "address": "5 Tahrir Street, building 2, apartment 9",
            "notes": "", "payment": "cash",
        })
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(ORDER_NOTIFICATION_EMAIL="")
    def test_missing_recipient_does_not_break_the_webhook(self):
        response = self.client.post(
            reverse("fawaterk_webhook"),
            data=json.dumps(valid_paid_payload(self.order)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(len(mail.outbox), 0)

    @patch("store.services.notifications.EmailMultiAlternatives.send",
           side_effect=Exception("SMTP is down"))
    def test_a_broken_mail_server_never_fails_the_webhook(self, mock_send):
        """Otherwise Fawaterk would retry the event forever."""
        response = self.client.post(
            reverse("fawaterk_webhook"),
            data=json.dumps(valid_paid_payload(self.order)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_cc_recipients_are_included(self):
        with override_settings(ORDER_NOTIFICATION_CC="assistant@example.com"):
            notifications.send_order_notification(self.order, reason="paid")
        self.assertEqual(mail.outbox[0].cc, ["assistant@example.com"])