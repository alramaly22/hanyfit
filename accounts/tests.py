"""Tests for the marketing pages (Online Coaching + Books).

The currency a visitor sees comes from where the site detects them (the
Vercel country header); there is no manual currency switcher any more. An
Egyptian visitor sees EGP, anyone else sees SAR, and the matching
product-specific payment link follows that currency.
"""

from django.test import TestCase
from django.urls import NoReverseMatch, reverse


class CoachingPricingPageTests(TestCase):
    def test_egyptian_visitor_sees_egp_by_default(self):
        response = self.client.get(reverse("pricing"))
        self.assertContains(response, 'data-currency="EGP"')
        self.assertContains(response, 'data-price="599"')
        self.assertContains(response, "https://app.fawaterk.com/pg/ashtrak-shhr1")
        self.assertNotContains(response, 'data-price="199"')

    def test_saudi_visitor_sees_sar_by_default(self):
        response = self.client.get(
            reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA"
        )
        self.assertContains(response, 'data-currency="SAR"')
        self.assertContains(response, 'data-price="199"')
        self.assertNotContains(response, 'data-price="599"')

    def test_there_is_no_currency_switcher_any_more(self):
        for name in ("index", "pricing", "book"):
            response = self.client.get(reverse(name))
            self.assertNotContains(response, "currency-selector")
            self.assertNotContains(response, "currency-switcher")
        with self.assertRaises(NoReverseMatch):
            reverse("set_currency")
        self.assertEqual(self.client.post("/set-currency/", {"currency": "SAR"}).status_code, 404)

    def test_pricing_page_has_four_packages(self):
        response = self.client.get(reverse("pricing"))
        self.assertContains(response, 'class="tier-wrap"', count=4)
        self.assertContains(response, "1 MONTH")
        self.assertContains(response, "2 MONTHS")
        self.assertContains(response, "3 MONTHS")
        self.assertContains(response, "ADVANCED")
        self.assertContains(response, "BD-ADVANCED-03")

    def test_advanced_package_follows_the_currency(self):
        # Egypt: 3,000 / 6,000 EGP.
        response = self.client.get(reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="EG")
        self.assertContains(response, 'data-price="3,000"')
        self.assertContains(response, 'data-price="6,000"')
        self.assertContains(response, "Save 3,000 EGP")
        self.assertNotContains(response, 'data-price="500"')
        self.assertNotContains(response, 'data-price="1,200"')
        self.assertEqual(
            response.context["advanced_link_1"], "https://app.fawaterk.com/pg/ghany"
        )
        self.assertEqual(
            response.context["advanced_link_3"], "https://app.fawaterk.com/pg/hany-m"
        )

        # Outside Egypt (e.g. Saudi Arabia): 500 / 1,200 SAR, never EGP.
        response = self.client.get(reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        self.assertContains(response, 'data-price="500"')
        self.assertContains(response, 'data-price="1,200"')
        self.assertContains(response, "Save 300 SAR")
        self.assertNotContains(response, 'data-price="3,000"')
        self.assertNotContains(response, 'data-price="6,000"')
        self.assertNotContains(response, "Save 3,000 EGP")
        self.assertEqual(
            response.context["advanced_link_1"], "https://app.fawaterk.com/pg/hany-e"
        )
        self.assertEqual(
            response.context["advanced_link_3"], "https://app.fawaterk.com/pg/astroyd"
        )

    def test_saudi_visitor_gets_sar_prices_on_the_three_coaching_packages(self):
        response = self.client.get(
            reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA"
        )
        for price in ("199", "299", "399"):
            self.assertContains(response, f'data-price="{price}"')


class CoachingPaymentButtonTests(TestCase):
    WA = "https://wa.me/966578079833"
    STEROID_EGP = ("https://app.fawaterk.com/pg/ghany", "https://app.fawaterk.com/pg/hany-m")

    def test_egp_regular_packages_have_pay_now_and_whatsapp(self):
        for name in ("pricing", "index"):
            response = self.client.get(reverse(name))
            for url in (
                "https://app.fawaterk.com/pg/ashtrak-shhr1",
                "https://app.fawaterk.com/pg/ashtrk-shhryn-2",
                "https://app.fawaterk.com/pg/shhr1",
            ):
                self.assertContains(response, f'href="{url}"')
            self.assertContains(response, 'data-lang="tierpaynow"', count=3)
            # 3 regular packages + the ADVANCED card, each with a WhatsApp button.
            self.assertContains(response, 'data-lang="tierwhatsapp"', count=4)
            self.assertNotContains(response, "paymentRequest/show/40531")

    def test_sar_regular_packages_use_the_body_transformation_links(self):
        response = self.client.get(reverse("pricing"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        self.assertContains(response, 'href="https://app.fawaterk.com/paymentRequest/show/40529"')
        self.assertContains(response, 'href="https://app.fawaterk.com/pg/body-upgrade"', count=2)
        for url in (
            "https://app.fawaterk.com/pg/ashtrak-shhr1",
            "https://app.fawaterk.com/pg/ashtrk-shhryn-2",
            "https://app.fawaterk.com/pg/shhr1",
        ):
            self.assertNotContains(response, url)

    def test_steroid_package_has_two_pay_buttons_and_whatsapp_in_each_currency(self):
        expected = {
            "EG": ("https://app.fawaterk.com/pg/ghany", "https://app.fawaterk.com/pg/hany-m"),
            "SA": ("https://app.fawaterk.com/pg/hany-e", "https://app.fawaterk.com/pg/astroyd"),
        }
        other = {"EG": expected["SA"], "SA": expected["EG"]}
        for country in ("EG", "SA"):
            for name in ("pricing", "index"):
                response = self.client.get(reverse(name), HTTP_X_VERCEL_IP_COUNTRY=country)
                for url in expected[country]:
                    self.assertContains(response, f'href="{url}"')
                # A visitor never sees the other currency's steroid links.
                for url in other[country]:
                    self.assertNotContains(response, url)
                self.assertContains(response, 'data-lang="tieradvpay1"', count=1)
                self.assertContains(response, 'data-lang="tieradvpay3"', count=1)


class CoachingIndexPageTests(TestCase):
    def test_index_page_follows_the_detected_country(self):
        response = self.client.get(reverse("index"))
        self.assertContains(response, 'data-price="599"')
        self.assertContains(response, 'class="tier-wrap"', count=4)

        response = self.client.get(reverse("index"), HTTP_X_VERCEL_IP_COUNTRY="SA")
        self.assertContains(response, 'data-price="299"')
        self.assertNotContains(response, 'data-price="1,099"')


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
