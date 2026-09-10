"""Tests for the marketing pages (Online Coaching + Books).

Covers the exact scenarios the country/currency audit specified: an
Egyptian visitor seeing EGP by default and being able to switch to SAR
(and vice versa for a Saudi visitor), with the correct product-specific
payment link following the selected currency, not just the country.
"""

from django.test import TestCase
from django.urls import reverse


class CoachingPricingPageTests(TestCase):
    def test_egyptian_visitor_sees_egp_by_default(self):
        response = self.client.get(reverse("pricing"))
        self.assertContains(response, "599 EGP")
        self.assertContains(response, "https://app.fawaterk.com/pg/ashtrak-shhr1")
        self.assertNotContains(response, "199 SAR")

    def test_saudi_visitor_sees_sar_by_default(self):
        response = self.client.get(
            reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA"
        )
        self.assertContains(response, "199 SAR")
        self.assertNotContains(response, "599 EGP")

    def test_egyptian_visitor_can_switch_to_sar(self):
        self.client.post(
            reverse("set_currency"), {"currency": "SAR", "next": reverse("pricing")}
        )
        response = self.client.get(reverse("pricing"))
        self.assertContains(response, "199 SAR")
        self.assertNotContains(response, "599 EGP")

    def test_saudi_visitor_can_switch_to_egp_and_gets_egp_payment_link(self):
        client = self.client
        client.get(reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        client.post(
            reverse("set_currency"), {"currency": "EGP", "next": reverse("pricing")}
        )
        response = client.get(reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        self.assertContains(response, "599 EGP")
        self.assertContains(response, "https://app.fawaterk.com/pg/ashtrak-shhr1")

    def test_switching_currency_never_changes_detected_country_context(self):
        # A Saudi visitor choosing EGP should not start seeing Egypt-only
        # store/shipping UI -- currency and country stay independent.
        from core import geo

        client = self.client
        client.get(reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        client.post(
            reverse("set_currency"), {"currency": "EGP", "next": reverse("pricing")}
        )
        response = client.get(reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        self.assertEqual(geo.detect_country(response.wsgi_request), "SA")


class CoachingIndexPageTests(TestCase):
    def test_index_page_reflects_selected_currency(self):
        response = self.client.get(reverse("index"))
        self.assertContains(response, "599 EGP")

        self.client.post(
            reverse("set_currency"), {"currency": "SAR", "next": reverse("index")}
        )
        response = self.client.get(reverse("index"))
        self.assertContains(response, "299 SAR")


class BooksPageTests(TestCase):
    def test_egyptian_visitor_sees_egp_book_links(self):
        response = self.client.get(reverse("book"))
        self.assertContains(response, "149 EGP")
        self.assertContains(response, "https://app.fawaterk.com/pg/ktab-osfat")
        self.assertContains(
            response, "https://app.fawaterk.com/pg/healthy-recipes-book"
        )

    def test_saudi_visitor_without_a_configured_sar_link_gets_whatsapp_fallback(self):
        response = self.client.get(reverse("book"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        self.assertContains(response, "50 SAR")
        self.assertContains(response, "wa.me")
