"""Tests for the centralized country/currency resolution (core app).

These are the regression tests for the exact problems the country/currency
audit found: two disconnected detection systems, a stale/legacy session
still showing an old currency after a deployment, and a client being able
to force an unsupported currency.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from . import currency, geo


class DetectCountryTests(TestCase):
    def test_defaults_to_egypt_when_header_absent(self):
        request = self.client.get("/").wsgi_request
        self.assertEqual(geo.detect_country(request), "EG")

    def test_reads_vercel_header(self):
        request = self.client.get("/", HTTP_X_VERCEL_IP_COUNTRY="SA").wsgi_request
        self.assertEqual(geo.detect_country(request), "SA")

    def test_header_is_case_and_whitespace_insensitive(self):
        request = self.client.get("/", HTTP_X_VERCEL_IP_COUNTRY=" sa ").wsgi_request
        self.assertEqual(geo.detect_country(request), "SA")

    def test_unrecognized_header_falls_back_to_default(self):
        # An empty/garbage header should behave like no header at all.
        request = self.client.get("/", HTTP_X_VERCEL_IP_COUNTRY="").wsgi_request
        self.assertEqual(geo.detect_country(request), "EG")


class DefaultCurrencyTests(TestCase):
    def test_egypt_defaults_to_egp(self):
        self.assertEqual(currency.get_default_currency("EG"), "EGP")

    def test_saudi_arabia_defaults_to_sar(self):
        self.assertEqual(currency.get_default_currency("SA"), "SAR")

    def test_unlisted_country_falls_back_to_sar(self):
        # Store only ships to EG/SA today; any other detected country
        # should still resolve to a sane default rather than error out.
        self.assertEqual(currency.get_default_currency("US"), "SAR")


class ResolveCurrencyTests(TestCase):
    def _request(self, **headers):
        return self.client.get("/", **headers).wsgi_request

    def test_no_override_uses_country_default(self):
        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="EG")
        self.assertEqual(currency.resolve_currency(request), "EGP")

        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="SA")
        self.assertEqual(currency.resolve_currency(request), "SAR")

    def test_valid_current_override_wins_over_country_default(self):
        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="EG")
        currency.set_currency_override(request, "SAR")
        self.assertEqual(currency.resolve_currency(request), "SAR")

    def test_override_never_changes_detected_country(self):
        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="EG")
        currency.set_currency_override(request, "SAR")
        # Country and currency are independent: overriding currency must
        # not be readable as "the visitor is now in Saudi Arabia".
        self.assertEqual(geo.detect_country(request), "EG")
        self.assertEqual(currency.resolve_currency(request), "SAR")

    def test_invalid_currency_is_rejected_and_not_stored(self):
        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="EG")
        ok = currency.set_currency_override(request, "USD")
        self.assertFalse(ok)
        self.assertEqual(currency.resolve_currency(request), "EGP")

    def test_legacy_session_with_no_version_is_ignored(self):
        """The exact bug from the audit: a session written before this
        system existed must not keep showing a stale currency forever."""
        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="EG")
        # Simulate a pre-existing session that has a currency value but was
        # never stamped with CURRENCY_CONTEXT_VERSION, because it predates
        # this system.
        request.session["selected_currency"] = "SAR"
        request.session.save()

        self.assertIsNone(currency.get_currency_override(request))
        self.assertEqual(currency.resolve_currency(request), "EGP")

    def test_session_with_old_version_is_ignored(self):
        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="EG")
        request.session["selected_currency"] = "SAR"
        request.session["currency_context_version"] = 0
        request.session.save()

        self.assertIsNone(currency.get_currency_override(request))
        self.assertEqual(currency.resolve_currency(request), "EGP")

    def test_a_future_currency_version_bump_invalidates_old_overrides(self):
        request = self._request(HTTP_X_VERCEL_IP_COUNTRY="EG")
        currency.set_currency_override(request, "SAR")
        self.assertEqual(currency.resolve_currency(request), "SAR")

        with override_settings(CURRENCY_CONTEXT_VERSION=999):
            # A version bump (as recommended whenever resolution logic
            # changes) makes the previously-valid override stale.
            self.assertIsNone(currency.get_currency_override(request))
            self.assertEqual(currency.resolve_currency(request), "EGP")


class SetCurrencyViewTests(TestCase):
    def test_valid_currency_is_persisted_across_requests(self):
        self.client.post(reverse("set_currency"), {"currency": "SAR", "next": "/pricing/"})
        response = self.client.get("/pricing/")
        self.assertContains(response, "199 SAR")
        self.assertNotContains(response, "599 EGP")

    def test_invalid_currency_is_rejected(self):
        response = self.client.post(
            reverse("set_currency"), {"currency": "USD", "next": "/pricing/"}
        )
        self.assertEqual(response.status_code, 302)
        follow = self.client.get("/pricing/")
        self.assertContains(follow, "599 EGP")

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("set_currency"))
        self.assertEqual(response.status_code, 405)

    def test_protocol_relative_next_is_rejected(self):
        """A "//evil.com" next value must never be followed off-site."""
        response = self.client.post(
            reverse("set_currency"), {"currency": "SAR", "next": "//evil.example.com"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")


class PaymentLinksTests(TestCase):
    def test_coaching_link_for_configured_currency(self):
        from . import payment_links

        self.assertIn(
            "app.fawaterk.com", payment_links.coaching_link("month_1", "EGP")
        )

    def test_coaching_link_falls_back_to_whatsapp_when_missing(self):
        from . import payment_links

        link = payment_links.coaching_link("month_1", "SAR")
        self.assertIn("wa.me", link)

    def test_book_link_falls_back_to_whatsapp_when_missing(self):
        from . import payment_links

        link = payment_links.book_link("recipe_mastery", "SAR", "ar")
        self.assertIn("wa.me", link)

    def test_book_link_for_configured_currency_and_language(self):
        from . import payment_links

        ar_link = payment_links.book_link("recipe_mastery", "EGP", "ar")
        en_link = payment_links.book_link("recipe_mastery", "EGP", "en")
        self.assertIn("app.fawaterk.com", ar_link)
        self.assertIn("app.fawaterk.com", en_link)
        self.assertNotEqual(ar_link, en_link)

    def test_unknown_product_falls_back_to_whatsapp(self):
        from . import payment_links

        self.assertIn("wa.me", payment_links.coaching_link("does_not_exist", "EGP"))
