"""Tests for the multi-country/currency system, the owner dashboard, and
visitor analytics added on top of the original store.

Kept in a separate module from store/tests.py (which already covers cart,
checkout, webhook and email behaviour in detail) so the two suites can be
read and run independently.
"""

import hashlib
import hmac
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .middleware import DEDUPE_WINDOW_SECONDS
from .models import Order, PageVisit, Product, ProductVariant
from .services import fawaterk

HASH_KEY = "test-hash-key"
REFUND_TOKEN = "test-refund-token"

TEST_SETTINGS = dict(
    FAWATERK_HASH_KEY=HASH_KEY,
    FAWATERK_REFUND_WEBHOOK_TOKEN=REFUND_TOKEN,
    FAWATERK_CLIENT_ID="test-client-id",
    FAWATERK_CLIENT_SECRET="test-client-secret",
    FAWATERK_DEBUG_LOGGING=False,
    ORDER_NOTIFICATION_EMAIL="",
    NOTIFY_ON_COD_ORDER=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FAWATERK_BASE_URL="https://staging.fawaterk.com",
    FAWATERK_VERIFY_WEBHOOK=True,
    TIKTOK_PIXEL_ID="",
    TIKTOK_ACCESS_TOKEN="",
    STORE_SHIPPING_FEE="60",
    STORE_FREE_SHIPPING_THRESHOLD="1000",
    STORE_MAX_ITEM_QUANTITY=10,
    STORE_CURRENCY="EGP",
    # Same reasoning as store/tests.py: the test client speaks plain http.
    SECURE_SSL_REDIRECT=False,
)


def make_product(slug="tee", price="100.00", stock=5, sizes=("S", "M", "L")):
    product = Product.objects.create(
        name=f"Product {slug}",
        slug=slug,
        description="A test garment.",
        price=Decimal(price),
        front_image="images/store/front.webp",
        back_image="images/store/back.webp",
    )
    for size in sizes:
        ProductVariant.objects.create(product=product, size=size, stock=stock)
    return product


def valid_paid_payload(order, invoice_id="12345", method="Card"):
    invoice_key = "INVKEY123"
    query = f"InvoiceId={invoice_id}&InvoiceKey={invoice_key}&PaymentMethod={method}"
    signature = hmac.new(HASH_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()
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
# Country / currency
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class CountrySelectionTests(TestCase):
    def setUp(self):
        self.product = make_product()

    def test_default_country_is_egypt_with_egp(self):
        response = self.client.get(reverse("store"))
        self.assertEqual(response.context["request"].session.get("store_country"), None)
        self.assertContains(response, "EGP")

    def test_switching_to_saudi_updates_session(self):
        response = self.client.post(
            reverse("set_country"), {"country": "SA", "next": "/store/"}
        )
        self.assertRedirects(response, "/store/")
        self.assertEqual(self.client.session.get("store_country"), "SA")

    def test_invalid_country_is_rejected(self):
        response = self.client.post(
            reverse("set_country"), {"country": "US", "next": "/store/"}
        )
        self.assertRedirects(response, "/store/")
        self.assertIsNone(self.client.session.get("store_country"))

    def test_open_redirect_is_blocked(self):
        """`next` must stay on-site, never redirect off to an attacker URL."""
        response = self.client.post(
            reverse("set_country"),
            {"country": "EG", "next": "https://evil.example.com/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/"))

    def test_egypt_prices_are_egp(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.context["display_price"], Decimal("100.00"))
        self.assertContains(response, "EGP")

    def test_saudi_prices_are_converted_and_labelled_sar(self):
        self.client.post(reverse("set_country"), {"country": "SA", "next": "/"})
        response = self.client.get(self.product.get_absolute_url())

        expected = Decimal("100.00") * Decimal("0.078")
        expected = expected.quantize(Decimal("0.01"))
        self.assertEqual(response.context["display_price"], expected)
        self.assertContains(response, "SAR")
        self.assertNotContains(response, ">EGP<")


@override_settings(**TEST_SETTINGS)
class CountryCheckoutTests(TestCase):
    """The country/currency stamped on an Order, end to end."""

    def setUp(self):
        self.product = make_product(price="100.00", stock=5)

    def add_to_cart(self, quantity=2):
        variant = self.product.available_variants()[0]
        self.client.post(
            reverse("cart_add"),
            {"slug": self.product.slug, "size": variant.size, "quantity": quantity},
        )
        return variant

    def valid_form(self, **overrides):
        data = {
            "full_name": "Ahmed Hassan",
            "phone": "01012345678",
            "email": "",
            "governorate": "Smouha",
            "city": "Nasr City",
            "address": "12 Abbas El Akkad Street, floor 3, apartment 7",
            "notes": "",
            "payment": "cash",
        }
        data.update(overrides)
        return data

    def test_egypt_checkout_requires_governorate(self):
        self.add_to_cart()
        response = self.client.post(
            reverse("checkout"), self.valid_form(governorate="")
        )
        self.assertFalse(Order.objects.exists())
        self.assertIn("governorate", response.context["form"].errors)

    def test_egypt_order_currency_and_shipping(self):
        self.add_to_cart(quantity=2)
        response = self.client.post(reverse("checkout"), self.valid_form())
        self.assertRedirects(response, Order.objects.get().get_absolute_url())

        order = Order.objects.get()
        self.assertEqual(order.country, "EG")
        self.assertEqual(order.currency, "EGP")
        self.assertEqual(order.subtotal, Decimal("200.00"))
        self.assertEqual(order.shipping_cost, Decimal("60.00"))
        self.assertEqual(order.subtotal + order.shipping_cost, order.total_price)

    def test_saudi_checkout_does_not_require_governorate(self):
        self.client.post(reverse("set_country"), {"country": "SA", "next": "/"})
        self.add_to_cart(quantity=1)

        response = self.client.post(
            reverse("checkout"),
            self.valid_form(governorate="", city="Riyadh", address="King Fahd Rd, bldg 4"),
        )
        order = Order.objects.get()
        self.assertRedirects(response, order.get_absolute_url())
        self.assertEqual(order.governorate, "")

    def test_saudi_order_currency_and_shipping(self):
        self.client.post(reverse("set_country"), {"country": "SA", "next": "/"})
        self.add_to_cart(quantity=2)

        self.client.post(
            reverse("checkout"),
            self.valid_form(governorate="", city="Riyadh", address="King Fahd Rd, bldg 4"),
        )
        order = Order.objects.get()
        self.assertEqual(order.country, "SA")
        self.assertEqual(order.currency, "SAR")
        self.assertEqual(order.shipping_cost, Decimal("25.00"))
        # 2 x 100.00 EGP -> SAR at the configured rate, exactly, using Decimal.
        expected_subtotal = (Decimal("100.00") * Decimal("0.078")).quantize(Decimal("0.01")) * 2
        self.assertEqual(order.subtotal, expected_subtotal)
        self.assertEqual(order.subtotal + order.shipping_cost, order.total_price)
        # No float ever touches this: everything above is Decimal arithmetic.
        self.assertIsInstance(order.subtotal, Decimal)

    def test_submitting_a_fake_egyptian_governorate_while_on_saudi_is_ignored(self):
        """A crafted POST cannot smuggle Egypt-only data into a Saudi order."""
        self.client.post(reverse("set_country"), {"country": "SA", "next": "/"})
        self.add_to_cart(quantity=1)

        self.client.post(
            reverse("checkout"),
            self.valid_form(governorate="Smouha", city="Riyadh", address="Some address here"),
        )
        order = Order.objects.get()
        self.assertEqual(order.country, "SA")
        self.assertEqual(order.governorate, "")  # discarded, not stored

    def test_switching_country_after_adding_to_cart_repriced_the_cart(self):
        """Country selection changes what is actually charged -- not just a label."""
        self.add_to_cart(quantity=1)  # added while on Egypt
        response = self.client.get(reverse("cart"))
        egypt_subtotal = response.context["subtotal"]

        self.client.post(reverse("set_country"), {"country": "SA", "next": "/"})
        response = self.client.get(reverse("cart"))
        saudi_subtotal = response.context["subtotal"]

        self.assertNotEqual(egypt_subtotal, saudi_subtotal)
        self.assertEqual(
            saudi_subtotal,
            (egypt_subtotal * Decimal("0.078")).quantize(Decimal("0.01")),
        )

    def test_client_supplied_country_field_is_ignored_by_the_form(self):
        """CheckoutForm takes the country from the session/view, never from POST."""
        self.add_to_cart(quantity=1)  # Egypt session
        data = self.valid_form()
        data["country"] = "SA"  # not a real form field; must have no effect
        self.client.post(reverse("checkout"), data)

        order = Order.objects.get()
        self.assertEqual(order.country, "EG")
        self.assertEqual(order.currency, "EGP")


# ---------------------------------------------------------------------------
# Fawaterk invoice currency
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class FawaterkCurrencyTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            full_name="Sara Ahmed",
            phone="0512345678",
            country="SA",
            city="Riyadh",
            address="King Fahd Rd",
            payment_method="online",
            subtotal=Decimal("15.60"),
            shipping_cost=Decimal("25.00"),
            total_price=Decimal("40.60"),
            currency="SAR",
        )
        product = make_product(slug="riyadh-tee", price="200.00")
        from .models import OrderItem

        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            size="M",
            quantity=1,
            unit_price=Decimal("15.60"),
        )

    def test_invoice_payload_uses_the_orders_own_currency(self):
        from unittest.mock import patch

        with patch("store.services.fawaterk.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "success",
                "data": {"url": "https://x/y", "invoiceKey": "K", "invoiceId": 1},
            }
            fawaterk.create_invoice(
                self.order,
                success_url="https://site.test/ok/",
                fail_url="https://site.test/fail/",
                pending_url="https://site.test/pending/",
            )
            payload = mock_post.call_args.kwargs["json"]
            self.assertEqual(payload["currency"], "SAR")

    def test_customer_address_has_no_dangling_comma_without_a_governorate(self):
        address = fawaterk._clean_address(
            ", ".join(
                part for part in (self.order.address, self.order.city, self.order.governorate) if part
            )
        )
        self.assertNotIn(",,", address)
        self.assertFalse(address.endswith(","))


# ---------------------------------------------------------------------------
# Refund webhook token
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class RefundWebhookTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            full_name="Ahmed Hassan",
            phone="01012345678",
            governorate="Smouha",
            city="Nasr City",
            address="12 Abbas El Akkad Street",
            payment_method="online",
            total_price=Decimal("510.00"),
            fawaterk_invoice_id="12345",
        )
        self.order.mark_paid(invoice_id="12345")

    def refund_payload(self):
        # _classify_webhook() recognises a refund by "approvedAt"/"reason",
        # not by invoice_status -- Fawaterk's refund callback shape.
        return {
            "invoice_id": "12345",
            "invoice_key": "INVKEY123",
            "approvedAt": "2026-08-02T00:00:00",
            "reason": "Customer requested",
            "pay_load": {"order_number": self.order.order_number},
        }

    def test_refund_without_the_shared_token_is_rejected(self):
        response = self.client.post(
            reverse("fawaterk_webhook"),
            data=json.dumps(self.refund_payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")  # unchanged

    def test_refund_with_the_correct_token_is_accepted(self):
        url = reverse("fawaterk_webhook") + f"?token={REFUND_TOKEN}"
        response = self.client.post(
            url, data=json.dumps(self.refund_payload()), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

    def test_refund_with_the_wrong_token_is_rejected(self):
        url = reverse("fawaterk_webhook") + "?token=wrong-value"
        response = self.client.post(
            url, data=json.dumps(self.refund_payload()), content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")


# ---------------------------------------------------------------------------
# Dashboard access control
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class DashboardAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", password="pw12345!", is_superuser=True, is_staff=True)
        self.staff_no_perm = User.objects.create_user(username="rando", password="pw12345!")

        from django.contrib.auth.models import Group
        self.group, _ = Group.objects.get_or_create(name="Store Dashboard")
        self.client_user = User.objects.create_user(username="client", password="pw12345!")
        self.client_user.groups.add(self.group)

        self.order = Order.objects.create(
            full_name="Ahmed Hassan", phone="01012345678", governorate="Smouha",
            city="Nasr City", address="12 Abbas El Akkad Street",
            payment_method="cash", total_price=Decimal("100.00"),
        )

    def test_anonymous_user_is_redirected_to_dashboard_login(self):
        response = self.client.get(reverse("dashboard_overview"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard_login"), response.url)

    def test_logged_in_user_without_permission_gets_403(self):
        self.client.login(username="rando", password="pw12345!")
        response = self.client.get(reverse("dashboard_overview"))
        self.assertEqual(response.status_code, 403)

    def test_group_member_can_access_the_dashboard(self):
        self.client.login(username="client", password="pw12345!")
        response = self.client.get(reverse("dashboard_overview"))
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access_the_dashboard(self):
        self.client.login(username="owner", password="pw12345!")
        response = self.client.get(reverse("dashboard_overview"))
        self.assertEqual(response.status_code, 200)

    def test_orders_list_requires_permission(self):
        response = self.client.get(reverse("dashboard_orders"))
        self.assertEqual(response.status_code, 302)

        self.client.login(username="rando", password="pw12345!")
        self.assertEqual(self.client.get(reverse("dashboard_orders")).status_code, 403)

    def test_order_detail_requires_permission(self):
        url = reverse("dashboard_order_detail", kwargs={"order_number": self.order.order_number})
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_status_update_requires_permission(self):
        url = reverse("dashboard_order_detail", kwargs={"order_number": self.order.order_number})
        response = self.client.post(url, {"order_status": "confirmed"})
        self.assertEqual(response.status_code, 302)  # bounced to login, not applied
        self.order.refresh_from_db()
        self.assertEqual(self.order.order_status, "new")

    def test_authorized_user_can_update_order_status(self):
        self.client.login(username="client", password="pw12345!")
        url = reverse("dashboard_order_detail", kwargs={"order_number": self.order.order_number})
        response = self.client.post(url, {"order_status": "confirmed"})
        self.assertRedirects(response, url)

        self.order.refresh_from_db()
        self.assertEqual(self.order.order_status, "confirmed")

    def test_status_update_via_get_is_not_applied(self):
        """The status can only change via POST (CSRF-protected form), never GET."""
        self.client.login(username="client", password="pw12345!")
        url = reverse("dashboard_order_detail", kwargs={"order_number": self.order.order_number})
        self.client.get(f"{url}?order_status=cancelled")

        self.order.refresh_from_db()
        self.assertEqual(self.order.order_status, "new")

    def test_unknown_order_number_is_404_not_an_error(self):
        """No IDOR/enumeration path: a made-up order_number is just a 404."""
        self.client.login(username="client", password="pw12345!")
        url = reverse("dashboard_order_detail", kwargs={"order_number": "HA-000000-FFFFFF"})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_dashboard_never_leaks_fawaterk_secret_into_the_page(self):
        self.client.login(username="client", password="pw12345!")
        response = self.client.get(reverse("dashboard_overview"))
        self.assertNotContains(response, HASH_KEY)

    def test_login_is_throttled_after_repeated_failures(self):
        from store.dashboard import LOGIN_MAX_ATTEMPTS

        for _ in range(LOGIN_MAX_ATTEMPTS):
            self.client.post(
                reverse("dashboard_login"),
                {"username": "client", "password": "wrong-password"},
            )
        # Even the correct password is rejected once locked out.
        response = self.client.post(
            reverse("dashboard_login"),
            {"username": "client", "password": "pw12345!"},
        )
        self.assertContains(response, "Too many failed attempts")
        self.assertFalse(response.wsgi_request.user.is_authenticated)


# ---------------------------------------------------------------------------
# Visitor analytics
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class VisitorTrackingTests(TestCase):
    def setUp(self):
        self.product = make_product()

    def test_visiting_the_store_records_a_page_visit(self):
        self.client.get(reverse("store"))
        self.assertEqual(PageVisit.objects.filter(path="/store/").count(), 1)

    def test_reloading_the_same_page_quickly_does_not_double_count(self):
        self.client.get(reverse("store"))
        self.client.get(reverse("store"))
        self.client.get(reverse("store"))
        self.assertEqual(PageVisit.objects.filter(path="/store/").count(), 1)

    def test_a_new_session_is_counted_separately(self):
        self.client.get(reverse("store"))
        other_client = self.client_class()
        other_client.get(reverse("store"))
        self.assertEqual(PageVisit.objects.filter(path="/store/").count(), 2)

    def test_dashboard_and_admin_paths_are_never_tracked(self):
        User = get_user_model()
        User.objects.create_user(username="owner", password="pw12345!", is_superuser=True, is_staff=True)
        self.client.login(username="owner", password="pw12345!")
        self.client.get(reverse("dashboard_overview"))
        self.assertFalse(PageVisit.objects.filter(path__startswith="/dashboard/").exists())

    def test_ajax_requests_are_not_tracked(self):
        self.client.post(
            reverse("cart_add"),
            {"slug": self.product.slug, "size": "M", "quantity": 1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertFalse(PageVisit.objects.filter(path="/store/cart/add/").exists())

    def test_the_dedupe_window_constant_is_a_sane_positive_number(self):
        # Guards against an accidental 0 (would defeat de-dupe entirely) or a
        # negative/huge value (would suppress real repeat visits for days).
        self.assertGreater(DEDUPE_WINDOW_SECONDS, 0)
        self.assertLessEqual(DEDUPE_WINDOW_SECONDS, 24 * 60 * 60)


# ---------------------------------------------------------------------------
# Price/currency manipulation via a tampered session
# ---------------------------------------------------------------------------

@override_settings(**TEST_SETTINGS)
class ManipulationTests(TestCase):
    def setUp(self):
        self.product = make_product(price="100.00", stock=5)

    def test_tampering_the_session_country_directly_still_prices_from_the_db(self):
        """Even a forged session value can only select a *real* configured
        country -- it can never inject an arbitrary price or exchange rate,
        because the cart re-reads the product price and the country's
        configured rate from the server on every request."""
        session = self.client.session
        session["store_country"] = "SA"
        session.save()

        response = self.client.get(self.product.get_absolute_url())
        expected = (Decimal("100.00") * Decimal("0.078")).quantize(Decimal("0.01"))
        self.assertEqual(response.context["display_price"], expected)

    def test_a_nonsense_session_country_falls_back_to_the_default(self):
        session = self.client.session
        session["store_country"] = "ZZ-not-a-real-country"
        session.save()

        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.context["display_price"], Decimal("100.00"))
