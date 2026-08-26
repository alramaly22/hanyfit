"""Tests for HanyFit Meals.

Mirrors store/tests.py's emphasis: the things that cost real money or trust
if they break -- filtering correctness, cart/checkout integration, capacity
enforcement, coupon math, and subscription creation. Apparel-cart regression
lives in store/tests.py (it already covers ProductVariant stock end to end);
this file adds one extra check that adding a meal never disturbs an apparel
line in the same cart.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from store.models import Order, OrderItem, Product, ProductVariant, SizeChoices
from .choices import Goal, MealType, PlanType
from .models import (
    Coupon,
    DeliveryClosedDate,
    DeliverySlot,
    Meal,
    MealDailyAvailability,
    Review,
    Subscription,
    SubscriptionDelivery,
    SubscriptionPlanPrice,
)
from .services import calculator as calculator_service
from .services import coupons as coupons_service
from .services import recommendation as recommendation_service
from .services import reviews as reviews_service
from .services import subscriptions as subscriptions_service
from .services.availability import MealUnavailable, is_available, is_delivery_open, reserve, release

TEST_SETTINGS = dict(
    SECURE_SSL_REDIRECT=False,
    STORE_MAX_ITEM_QUANTITY=10,
    MEALS_MAX_CART_QUANTITY=10,
    MEALS_DELIVERY_WINDOW_DAYS=14,
    MEALS_HIGH_PROTEIN_THRESHOLD_G=25,
    STORE_CURRENCY="EGP",
    FAWATERK_CLIENT_ID="",
    FAWATERK_CLIENT_SECRET="",
)


def make_meal(**kwargs):
    defaults = dict(
        name="Test Meal",
        slug="test-meal",
        price=Decimal("100.00"),
        calories=500,
        protein_g=Decimal("30.0"),
        carbs_g=Decimal("40.0"),
        fat_g=Decimal("10.0"),
        goal=Goal.FAT_LOSS,
        meal_type=MealType.LUNCH,
        is_active=True,
    )
    defaults.update(kwargs)
    return Meal.objects.create(**defaults)


def make_slot(weekday=None, cutoff_hours_before=0):
    weekday = timezone.localdate().weekday() if weekday is None else weekday
    return DeliverySlot.objects.create(
        weekday=weekday,
        start_time="12:00",
        end_time="14:00",
        is_active=True,
        cutoff_hours_before=cutoff_hours_before,
    )


def next_bookable(slot_qs):
    """First (date, slot) pair, within the delivery window, that is bookable."""
    for offset in range(1, 10):
        date = timezone.localdate() + timedelta(days=offset)
        for slot in slot_qs:
            if slot.is_bookable_for(date):
                return date, slot
    raise AssertionError("No bookable date/slot found in range")


@override_settings(**TEST_SETTINGS)
class MealFilterTests(TestCase):
    def setUp(self):
        self.gain = make_meal(
            name="Gain Meal", slug="gain-meal", goal=Goal.MUSCLE_GAIN,
            meal_type=MealType.BREAKFAST, calories=700, protein_g=Decimal("20.0"),
        )
        self.loss_lunch_lowcal_highprotein = make_meal(
            name="Loss Meal", slug="loss-meal", goal=Goal.FAT_LOSS,
            meal_type=MealType.LUNCH, calories=400, protein_g=Decimal("35.0"),
        )
        self.maintain = make_meal(
            name="Maintain Meal", slug="maintain-meal", goal=Goal.MAINTAIN,
            meal_type=MealType.DINNER, calories=600, protein_g=Decimal("15.0"),
        )
        self.inactive = make_meal(
            name="Inactive Meal", slug="inactive-meal", is_active=False,
        )

    def test_active_excludes_inactive(self):
        self.assertNotIn(self.inactive, list(Meal.objects.active()))

    def test_goal_filter(self):
        results = list(Meal.objects.active().for_goal(Goal.FAT_LOSS))
        self.assertEqual(results, [self.loss_lunch_lowcal_highprotein])

    def test_meal_type_filter(self):
        results = list(Meal.objects.active().for_meal_type(MealType.DINNER))
        self.assertEqual(results, [self.maintain])

    def test_under_calories_filter(self):
        results = list(Meal.objects.active().under_calories(500))
        self.assertEqual(results, [self.loss_lunch_lowcal_highprotein])

    def test_high_protein_filter(self):
        results = list(Meal.objects.active().high_protein())
        self.assertEqual(results, [self.loss_lunch_lowcal_highprotein])

    def test_combined_filters_via_view(self):
        response = self.client.get(
            reverse("meals:meal_list"),
            {"goal": Goal.FAT_LOSS, "meal_type": MealType.LUNCH, "under_500": "1", "high_protein": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["meals"]), [self.loss_lunch_lowcal_highprotein])


@override_settings(**TEST_SETTINGS)
class MealAvailabilityTests(TestCase):
    def test_unlimited_by_default(self):
        meal = make_meal(daily_capacity=0)
        self.assertTrue(meal.is_unlimited)
        self.assertTrue(is_available(meal, timezone.localdate(), quantity=999))

    def test_reserve_respects_capacity(self):
        meal = make_meal(daily_capacity=5)
        date = timezone.localdate()
        reserve(meal.pk, date, 3)
        self.assertTrue(is_available(meal, date, quantity=2))
        self.assertFalse(is_available(meal, date, quantity=3))
        with self.assertRaises(MealUnavailable):
            reserve(meal.pk, date, 3)

    def test_release_frees_capacity(self):
        meal = make_meal(daily_capacity=5)
        date = timezone.localdate()
        reserve(meal.pk, date, 5)
        self.assertFalse(is_available(meal, date, quantity=1))
        release(meal.pk, date, 2)
        self.assertTrue(is_available(meal, date, quantity=2))
        self.assertFalse(is_available(meal, date, quantity=3))

    def test_closed_date_override_blocks_even_when_room(self):
        meal = make_meal(daily_capacity=50)
        date = timezone.localdate()
        MealDailyAvailability.objects.create(meal=meal, date=date, is_closed=True)
        self.assertFalse(is_available(meal, date, quantity=1))
        with self.assertRaises(MealUnavailable):
            reserve(meal.pk, date, 1)

    def test_per_date_capacity_override(self):
        meal = make_meal(daily_capacity=50)
        date = timezone.localdate()
        MealDailyAvailability.objects.create(meal=meal, date=date, capacity_override=2)
        self.assertTrue(is_available(meal, date, quantity=2))
        self.assertFalse(is_available(meal, date, quantity=3))


@override_settings(**TEST_SETTINGS)
class CartIntegrationTests(TestCase):
    def setUp(self):
        self.meal = make_meal(price=Decimal("120.00"), daily_capacity=0)
        self.product = Product.objects.create(
            name="Tee", slug="tee", price=Decimal("300.00"), is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, size=SizeChoices.M, stock=10, is_active=True,
        )

    def test_add_meal_to_cart(self):
        response = self.client.post(
            reverse("cart_add_meal"), {"slug": self.meal.slug, "quantity": 2}
        )
        self.assertEqual(response.status_code, 302)
        cart_response = self.client.get(reverse("cart"))
        self.assertContains(cart_response, self.meal.name)
        self.assertEqual(self.client.session["cart"][f"meal:{self.meal.pk}"]["quantity"], 2)

    def test_mixed_cart_does_not_disturb_apparel_line(self):
        self.client.post(
            reverse("cart_add"),
            {"slug": self.product.slug, "size": SizeChoices.M, "quantity": 1},
        )
        self.client.post(
            reverse("cart_add_meal"), {"slug": self.meal.slug, "quantity": 1}
        )
        session_cart = self.client.session["cart"]
        self.assertEqual(len(session_cart), 2)
        apparel_key = f"{self.product.pk}:{SizeChoices.M}"
        self.assertIn(apparel_key, session_cart)
        self.assertEqual(session_cart[apparel_key]["quantity"], 1)

    def test_remove_meal_line(self):
        self.client.post(
            reverse("cart_add_meal"), {"slug": self.meal.slug, "quantity": 1}
        )
        key = f"meal:{self.meal.pk}"
        self.client.post(reverse("cart_remove"), {"key": key})
        self.assertNotIn(key, self.client.session.get("cart", {}))

    def test_update_meal_quantity(self):
        self.client.post(
            reverse("cart_add_meal"), {"slug": self.meal.slug, "quantity": 1}
        )
        key = f"meal:{self.meal.pk}"
        self.client.post(reverse("cart_update"), {"key": key, "quantity": 3})
        self.assertEqual(self.client.session["cart"][key]["quantity"], 3)


@override_settings(**TEST_SETTINGS)
class CheckoutTests(TestCase):
    def setUp(self):
        self.meal = make_meal(price=Decimal("120.00"), daily_capacity=2)
        self.slot = make_slot()
        self.client.post(reverse("cart_add_meal"), {"slug": self.meal.slug, "quantity": 1})
        self.date, self.slot = next_bookable(DeliverySlot.objects.filter(is_active=True))

    def _valid_payload(self, **overrides):
        payload = {
            "full_name": "Ahmed Hassan",
            "phone": "01012345678",
            "email": "ahmed@example.com",
            "governorate": "Cairo",
            "city": "Nasr City",
            "address": "123 Test street, building 5, floor 2",
            "notes": "",
            "payment": "cash",
            "delivery_date": self.date.isoformat(),
            "delivery_slot": self.slot.pk,
            "coupon_code": "",
        }
        payload.update(overrides)
        return payload

    def test_valid_meal_checkout_creates_order(self):
        response = self.client.post(reverse("checkout"), self._valid_payload())
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.delivery_date, self.date)
        self.assertEqual(order.items.get().item_type, OrderItem.ItemType.MEAL)
        self.assertEqual(order.subtotal, Decimal("120.00"))

    def test_missing_delivery_date_rejected_for_meal_order(self):
        response = self.client.post(
            reverse("checkout"), self._valid_payload(delivery_date="", delivery_slot="")
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.assertFalse(Order.objects.exists())

    def test_delivery_date_in_past_rejected(self):
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.client.post(reverse("checkout"), self._valid_payload(delivery_date=past))
        self.assertFalse(Order.objects.exists())
        self.assertContains(response, "future")

    def test_capacity_enforced_at_checkout(self):
        # Exhaust the meal's capacity for that date first.
        reserve(self.meal.pk, self.date, 2)
        response = self.client.post(reverse("checkout"), self._valid_payload())
        self.assertFalse(Order.objects.exists())
        self.assertEqual(response.status_code, 302)  # redirected back to cart with an error
        self.client.session.clear()

    def test_price_cannot_be_tampered_from_client(self):
        # There is no client-controlled price field on the checkout form at
        # all -- the total is always cart.total, recomputed server-side. This
        # asserts that posting an unrelated extra field cannot change it.
        response = self.client.post(
            reverse("checkout"), self._valid_payload(total="1.00", subtotal="1.00")
        )
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.subtotal, Decimal("120.00"))


@override_settings(**TEST_SETTINGS)
class CouponTests(TestCase):
    def setUp(self):
        self.coupon = Coupon.objects.create(
            code="save10", discount_type=Coupon.DiscountType.PERCENT,
            discount_value=Decimal("10"), applies_to=Coupon.AppliesTo.BOTH,
        )

    def test_code_is_normalised_uppercase(self):
        self.assertEqual(self.coupon.code, "SAVE10")

    def test_valid_coupon_discount(self):
        discount = self.coupon.calculate_discount(Decimal("200.00"))
        self.assertEqual(discount, Decimal("20.00"))

    def test_discount_never_exceeds_subtotal(self):
        self.coupon.discount_type = Coupon.DiscountType.FIXED
        self.coupon.discount_value = Decimal("500.00")
        self.coupon.save()
        discount = self.coupon.calculate_discount(Decimal("50.00"))
        self.assertEqual(discount, Decimal("50.00"))

    def test_expired_coupon_rejected(self):
        self.coupon.expires_at = timezone.now() - timedelta(days=1)
        self.coupon.save()
        with self.assertRaises(coupons_service.CouponError):
            coupons_service.validate("SAVE10", subtotal=Decimal("100"), phone="010", kind="meals")

    def test_invalid_code_rejected(self):
        with self.assertRaises(coupons_service.CouponError):
            coupons_service.validate("NOPE", subtotal=Decimal("100"), phone="010", kind="meals")

    def test_usage_limit_enforced(self):
        self.coupon.usage_limit = 1
        self.coupon.save()
        coupons_service.redeem(self.coupon.pk, subtotal=Decimal("100"), phone="010")
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 1)
        with self.assertRaises(coupons_service.CouponError):
            coupons_service.redeem(self.coupon.pk, subtotal=Decimal("100"), phone="011")

    def test_full_checkout_price_recalculated_with_coupon(self):
        meal = make_meal(price=Decimal("200.00"), daily_capacity=0)
        slot = make_slot()
        date, slot = next_bookable(DeliverySlot.objects.filter(is_active=True))
        self.client.post(reverse("cart_add_meal"), {"slug": meal.slug, "quantity": 1})
        payload = {
            "full_name": "Ahmed Hassan", "phone": "01012345678", "email": "a@a.com",
            "governorate": "Cairo", "city": "Nasr City",
            "address": "123 Test street, building 5, floor 2", "notes": "",
            "payment": "cash", "delivery_date": date.isoformat(), "delivery_slot": slot.pk,
            "coupon_code": "SAVE10",
        }
        response = self.client.post(reverse("checkout"), payload)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.discount_amount, Decimal("20.00"))
        self.assertEqual(order.total_price, order.subtotal + order.shipping_cost - Decimal("20.00"))

    def test_applies_to_kind_restricts_meals_only_coupon(self):
        self.coupon.applies_to = Coupon.AppliesTo.MEALS
        self.coupon.save()
        self.assertTrue(self.coupon.applies_to_kind("meals"))
        self.assertFalse(self.coupon.applies_to_kind("subscriptions"))


@override_settings(**TEST_SETTINGS)
class SubscriptionTests(TestCase):
    def setUp(self):
        SubscriptionPlanPrice.objects.create(
            plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            price=Decimal("700.00"),
        )
        SubscriptionPlanPrice.objects.create(
            plan_type=PlanType.MONTHLY, goal=Goal.MUSCLE_GAIN, meals_per_day=1,
            price=Decimal("3000.00"),
        )
        self.slot = make_slot()

    def test_create_weekly_subscription_builds_full_schedule(self):
        subscription = subscriptions_service.create_subscription(
            full_name="Sara Ali", phone="01011112222", email="",
            governorate="Giza", city="Dokki", address="10 Test Ave, building 2",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=timezone.localdate() + timedelta(days=1), delivery_slot=self.slot,
            payment_method=Subscription.PaymentMethod.CASH,
        )
        self.assertEqual(subscription.price, Decimal("700.00"))
        self.assertEqual(subscription.deliveries.count(), 7)
        self.assertEqual(
            subscription.end_date, subscription.start_date + timedelta(days=6)
        )

    def test_create_monthly_subscription_builds_30_days(self):
        subscription = subscriptions_service.create_subscription(
            full_name="Omar", phone="01022223333", email="",
            governorate="Giza", city="Dokki", address="10 Test Ave, building 2",
            notes="", plan_type=PlanType.MONTHLY, goal=Goal.MUSCLE_GAIN, meals_per_day=1,
            start_date=timezone.localdate() + timedelta(days=1), delivery_slot=self.slot,
            payment_method=Subscription.PaymentMethod.CASH,
        )
        self.assertEqual(subscription.deliveries.count(), 30)

    def test_missing_plan_price_raises(self):
        with self.assertRaises(subscriptions_service.SubscriptionError):
            subscriptions_service.create_subscription(
                full_name="X", phone="01000000000", email="",
                governorate="Giza", city="Dokki", address="10 Test Ave, building 2",
                notes="", plan_type=PlanType.MONTHLY, goal=Goal.FAT_LOSS, meals_per_day=1,
                start_date=timezone.localdate() + timedelta(days=1), delivery_slot=self.slot,
                payment_method=Subscription.PaymentMethod.CASH,
            )

    def test_status_change_skips_remaining_deliveries(self):
        subscription = subscriptions_service.create_subscription(
            full_name="Sara Ali", phone="01011112222", email="",
            governorate="Giza", city="Dokki", address="10 Test Ave, building 2",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=timezone.localdate() + timedelta(days=1), delivery_slot=self.slot,
            payment_method=Subscription.PaymentMethod.CASH,
        )
        subscriptions_service.set_status(subscription, Subscription.Status.PAUSED)
        self.assertEqual(
            subscription.deliveries.filter(status=SubscriptionDelivery.Status.SKIPPED).count(), 7
        )

    def test_subscribe_view_creates_subscription_end_to_end(self):
        date, slot = next_bookable(DeliverySlot.objects.filter(is_active=True))
        payload = {
            "full_name": "Sara Ali", "phone": "01011112222", "email": "",
            "goal": Goal.FAT_LOSS, "meals_per_day": "1",
            "start_date": date.isoformat(), "delivery_slot": slot.pk,
            "governorate": "Giza", "city": "Dokki", "address": "10 Test Ave, building 2",
            "notes": "", "payment_method": "cash", "coupon_code": "",
        }
        response = self.client.post(reverse("meals:subscribe", kwargs={"plan_type": "weekly"}), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Subscription.objects.count(), 1)

    def test_delivery_time_choice_is_independent_of_start_date_weekday(self):
        """Regression test: a subscription delivers every day, so picking a
        6-8pm slot that happens to be stored against "Monday" must not be
        rejected just because start_date falls on a different weekday."""
        weekday_slot = DeliverySlot.objects.filter(is_active=True).first()
        # Pick a start_date guaranteed to be on a *different* weekday than
        # the slot's own weekday, so this only passes if weekday is ignored.
        start_date = timezone.localdate() + timedelta(days=1)
        while start_date.weekday() == weekday_slot.weekday:
            start_date += timedelta(days=1)

        payload = {
            "full_name": "Nour", "phone": "01033334444", "email": "",
            "goal": Goal.FAT_LOSS, "meals_per_day": "1",
            "start_date": start_date.isoformat(), "delivery_slot": weekday_slot.pk,
            "governorate": "Giza", "city": "Dokki", "address": "10 Test Ave, building 2",
            "notes": "", "payment_method": "cash", "coupon_code": "",
        }
        response = self.client.post(reverse("meals:subscribe", kwargs={"plan_type": "weekly"}), payload)
        self.assertEqual(response.status_code, 302, "subscription should not be rejected for a weekday mismatch")

    def test_delivery_slot_choices_deduplicated_by_time_window(self):
        response = self.client.get(reverse("meals:subscribe", kwargs={"plan_type": "weekly"}))
        choices = list(response.context["form"].fields["delivery_slot"].queryset)
        time_windows = {(slot.start_time, slot.end_time) for slot in choices}
        self.assertEqual(len(choices), len(time_windows), "each time window should appear once, not once per weekday")


@override_settings(**TEST_SETTINGS)
class CalculatorTests(TestCase):
    def test_estimate_within_sane_range(self):
        calories = calculator_service.estimate_daily_calories(25, 180, 80, Goal.MAINTAIN)
        self.assertGreater(calories, 1200)
        self.assertLess(calories, 5000)

    def test_muscle_gain_higher_than_fat_loss_for_same_body(self):
        gain = calculator_service.estimate_daily_calories(25, 180, 80, Goal.MUSCLE_GAIN)
        loss = calculator_service.estimate_daily_calories(25, 180, 80, Goal.FAT_LOSS)
        self.assertGreater(gain, loss)

    def test_out_of_range_inputs_rejected(self):
        with self.assertRaises(calculator_service.CalculatorInputError):
            calculator_service.estimate_daily_calories(5, 180, 80, Goal.MAINTAIN)
        with self.assertRaises(calculator_service.CalculatorInputError):
            calculator_service.estimate_daily_calories(25, 500, 80, Goal.MAINTAIN)

    def test_recommend_meals_only_returns_matching_goal(self):
        make_meal(name="Gain", slug="gain", goal=Goal.MUSCLE_GAIN)
        make_meal(name="Loss", slug="loss", goal=Goal.FAT_LOSS)
        recommended = calculator_service.recommend_meals(Goal.MUSCLE_GAIN, 2000)
        self.assertTrue(all(meal.goal == Goal.MUSCLE_GAIN for meal in recommended))

    def test_recommend_subscription_plans_only_lists_configured_ones(self):
        SubscriptionPlanPrice.objects.create(
            plan_type=PlanType.WEEKLY, goal=Goal.MAINTAIN, meals_per_day=1, price=Decimal("650.00"),
        )
        plans = calculator_service.recommend_subscription_plans(Goal.MAINTAIN, meals_per_day=1)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["plan_type"], "weekly")

    def test_calculator_view_end_to_end(self):
        response = self.client.post(
            reverse("meals:calculator"),
            {"age": 25, "height_cm": 180, "weight_kg": 80, "goal": Goal.MUSCLE_GAIN},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["result"])
        self.assertIn("calories", response.context["result"])


@override_settings(**TEST_SETTINGS)
class SubscriptionPreferencesAndWeekdayTests(TestCase):
    def setUp(self):
        SubscriptionPlanPrice.objects.create(
            plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1, price=Decimal("700.00"),
        )
        self.slot = make_slot()

    def test_delivery_weekdays_restricts_generated_deliveries(self):
        start = timezone.localdate() + timedelta(days=1)
        # Every weekday except the ones actually chosen.
        chosen = [0, 2, 4]
        subscription = subscriptions_service.create_subscription(
            full_name="X", phone="01000000001", email="",
            governorate="Cairo", city="Nasr City", address="1 test street building 1",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=start, delivery_slot=self.slot, payment_method=Subscription.PaymentMethod.CASH,
            delivery_weekdays=chosen,
        )
        for delivery in subscription.deliveries.all():
            self.assertIn(delivery.scheduled_date.weekday(), chosen)

    def test_empty_delivery_weekdays_means_every_day(self):
        start = timezone.localdate() + timedelta(days=1)
        subscription = subscriptions_service.create_subscription(
            full_name="X", phone="01000000002", email="",
            governorate="Cairo", city="Nasr City", address="1 test street building 1",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=start, delivery_slot=self.slot, payment_method=Subscription.PaymentMethod.CASH,
        )
        self.assertEqual(subscription.deliveries.count(), 7)

    def test_closed_date_is_skipped_in_generated_schedule(self):
        start = timezone.localdate() + timedelta(days=1)
        DeliveryClosedDate.objects.create(date=start + timedelta(days=2))
        subscription = subscriptions_service.create_subscription(
            full_name="X", phone="01000000003", email="",
            governorate="Cairo", city="Nasr City", address="1 test street building 1",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=start, delivery_slot=self.slot, payment_method=Subscription.PaymentMethod.CASH,
        )
        self.assertEqual(subscription.deliveries.count(), 6)

    def test_preferences_are_stored(self):
        start = timezone.localdate() + timedelta(days=1)
        subscription = subscriptions_service.create_subscription(
            full_name="X", phone="01000000004", email="",
            governorate="Cairo", city="Nasr City", address="1 test street building 1",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=start, delivery_slot=self.slot, payment_method=Subscription.PaymentMethod.CASH,
            allergies="peanuts", disliked_foods="mushrooms", dietary_notes="low sodium",
        )
        subscription.refresh_from_db()
        self.assertEqual(subscription.allergies, "peanuts")
        self.assertEqual(subscription.disliked_foods, "mushrooms")
        self.assertEqual(subscription.dietary_notes, "low sodium")


@override_settings(**TEST_SETTINGS)
class SubscriptionSelfServiceTests(TestCase):
    def setUp(self):
        SubscriptionPlanPrice.objects.create(
            plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1, price=Decimal("700.00"),
        )
        self.slot = make_slot()
        self.subscription = subscriptions_service.create_subscription(
            full_name="Nour", phone="01000000005", email="",
            governorate="Cairo", city="Nasr City", address="1 test street building 1",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=timezone.localdate() + timedelta(days=1), delivery_slot=self.slot,
            payment_method=Subscription.PaymentMethod.CASH,
        )

    def test_pause_skips_future_scheduled_deliveries(self):
        subscriptions_service.pause(self.subscription)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PAUSED)
        self.assertEqual(
            self.subscription.deliveries.filter(status=SubscriptionDelivery.Status.SKIPPED).count(), 7
        )

    def test_resume_restores_only_auto_skipped_deliveries(self):
        # Customer skips one delivery on purpose first.
        manually_skipped = self.subscription.deliveries.first()
        subscriptions_service.skip_delivery(manually_skipped)

        subscriptions_service.pause(self.subscription)
        subscriptions_service.resume(self.subscription)

        manually_skipped.refresh_from_db()
        self.assertEqual(manually_skipped.status, SubscriptionDelivery.Status.SKIPPED)
        self.assertEqual(
            self.subscription.deliveries.filter(status=SubscriptionDelivery.Status.SCHEDULED).count(), 6
        )

    def test_cannot_pause_twice(self):
        subscriptions_service.pause(self.subscription)
        with self.assertRaises(subscriptions_service.SubscriptionError):
            subscriptions_service.pause(self.subscription)

    def test_skip_today_or_past_delivery_rejected(self):
        delivery = self.subscription.deliveries.first()
        delivery.scheduled_date = timezone.localdate()
        delivery.save()
        with self.assertRaises(subscriptions_service.SubscriptionError):
            subscriptions_service.skip_delivery(delivery)

    def test_reschedule_respects_delivery_weekdays(self):
        self.subscription.delivery_weekdays = [0]
        self.subscription.save()
        delivery = self.subscription.deliveries.first()
        bad_date = timezone.localdate() + timedelta(days=1)
        while bad_date.weekday() == 0:
            bad_date += timedelta(days=1)
        with self.assertRaises(subscriptions_service.SubscriptionError):
            subscriptions_service.reschedule_delivery(delivery, bad_date)

    def test_reschedule_onto_closed_date_rejected(self):
        delivery = self.subscription.deliveries.first()
        closed_date = self.subscription.start_date + timedelta(days=3)
        DeliveryClosedDate.objects.create(date=closed_date)
        with self.assertRaises(subscriptions_service.SubscriptionError):
            subscriptions_service.reschedule_delivery(delivery, closed_date)

    def test_change_address_updates_fields(self):
        subscriptions_service.change_address(
            self.subscription, governorate="Giza", city="Dokki", address="New address here",
        )
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.city, "Dokki")

    def test_renew_creates_linked_subscription_after_end_date(self):
        renewed = subscriptions_service.renew(self.subscription)
        self.assertEqual(renewed.renewed_from_id, self.subscription.pk)
        self.assertGreater(renewed.start_date, self.subscription.end_date)

    def test_cannot_renew_before_current_plan_ends(self):
        with self.assertRaises(subscriptions_service.SubscriptionError):
            subscriptions_service.renew(self.subscription, start_date=timezone.localdate() + timedelta(days=1))

    def test_customer_a_cannot_reach_customer_bs_subscription(self):
        other = subscriptions_service.create_subscription(
            full_name="Other", phone="01000000009", email="",
            governorate="Cairo", city="Nasr City", address="9 other street building 1",
            notes="", plan_type=PlanType.WEEKLY, goal=Goal.FAT_LOSS, meals_per_day=1,
            start_date=timezone.localdate() + timedelta(days=1), delivery_slot=self.slot,
            payment_method=Subscription.PaymentMethod.CASH,
        )
        # Right pk, wrong token -- must not resolve to the other subscription.
        url = reverse(
            "meals:subscription_status", kwargs={"pk": other.pk, "token": self.subscription.access_token}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_self_service_actions_require_post(self):
        url = reverse(
            "meals:subscription_pause",
            kwargs={"pk": self.subscription.pk, "token": self.subscription.access_token},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_pause_view_end_to_end(self):
        url = reverse(
            "meals:subscription_pause",
            kwargs={"pk": self.subscription.pk, "token": self.subscription.access_token},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PAUSED)


@override_settings(**TEST_SETTINGS)
class ReviewTests(TestCase):
    def setUp(self):
        self.meal = make_meal(price=Decimal("100.00"), daily_capacity=0)
        self.slot = make_slot()

    def _delivered_order_with_meal_item(self):
        date, slot = next_bookable(DeliverySlot.objects.filter(is_active=True))
        self.client.post(reverse("cart_add_meal"), {"slug": self.meal.slug, "quantity": 1})
        payload = {
            "full_name": "Reviewer", "phone": "01000000010", "email": "r@r.com",
            "governorate": "Cairo", "city": "Nasr City", "address": "1 review street building 1",
            "notes": "", "payment": "cash", "delivery_date": date.isoformat(), "delivery_slot": slot.pk,
            "coupon_code": "",
        }
        self.client.post(reverse("checkout"), payload)
        order = Order.objects.latest("id")
        order.order_status = Order.OrderStatus.DELIVERED
        order.save(update_fields=["order_status"])
        return order, order.items.get()

    def test_cannot_review_undelivered_order(self):
        date, slot = next_bookable(DeliverySlot.objects.filter(is_active=True))
        self.client.post(reverse("cart_add_meal"), {"slug": self.meal.slug, "quantity": 1})
        payload = {
            "full_name": "Reviewer", "phone": "01000000011", "email": "",
            "governorate": "Cairo", "city": "Nasr City", "address": "1 review street building 1",
            "notes": "", "payment": "cash", "delivery_date": date.isoformat(), "delivery_slot": slot.pk,
            "coupon_code": "",
        }
        self.client.post(reverse("checkout"), payload)
        order = Order.objects.latest("id")
        item = order.items.get()
        with self.assertRaises(reviews_service.ReviewError):
            reviews_service.create_review(item, rating=5)

    def test_review_delivered_order_succeeds_and_updates_average(self):
        order, item = self._delivered_order_with_meal_item()
        reviews_service.create_review(item, rating=4, comment="Nice")
        self.meal.refresh_from_db()
        self.assertEqual(self.meal.average_rating, 4)
        self.assertEqual(self.meal.review_count, 1)

    def test_duplicate_review_rejected(self):
        order, item = self._delivered_order_with_meal_item()
        reviews_service.create_review(item, rating=4)
        with self.assertRaises(reviews_service.ReviewError):
            reviews_service.create_review(item, rating=2)

    def test_hidden_review_excluded_from_average(self):
        order, item = self._delivered_order_with_meal_item()
        review = reviews_service.create_review(item, rating=1)
        review.is_hidden = True
        review.save()
        self.meal.refresh_from_db()
        self.assertIsNone(self.meal.average_rating)

    def test_review_view_end_to_end(self):
        order, item = self._delivered_order_with_meal_item()
        url = reverse(
            "meals:add_review",
            kwargs={"order_number": order.order_number, "token": order.access_token, "item_id": item.pk},
        )
        response = self.client.post(url, {"rating": 5, "comment": "Loved it"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Review.objects.filter(order_item=item).exists())


@override_settings(**TEST_SETTINGS)
class RecommendationServiceTests(TestCase):
    def test_allergy_keyword_excludes_matching_meals(self):
        make_meal(name="Peanut Bowl", slug="peanut-bowl", ingredients="peanuts, rice", goal=Goal.FAT_LOSS)
        safe = make_meal(name="Chicken Bowl", slug="chicken-bowl", ingredients="chicken, rice", goal=Goal.FAT_LOSS)
        results = recommendation_service.recommend_meals(Goal.FAT_LOSS, allergies_text="peanuts")
        self.assertIn(safe, results)
        self.assertNotIn(Meal.objects.get(slug="peanut-bowl"), results)

    def test_disliked_food_keyword_excludes_matching_meals(self):
        make_meal(name="Mushroom Risotto", slug="mushroom-risotto", ingredients="mushrooms, rice", goal=Goal.MAINTAIN)
        safe = make_meal(name="Plain Rice Bowl", slug="plain-rice-bowl", ingredients="rice, chicken", goal=Goal.MAINTAIN)
        results = recommendation_service.recommend_meals(Goal.MAINTAIN, disliked_foods_text="mushrooms")
        self.assertIn(safe, results)
        self.assertNotIn(Meal.objects.get(slug="mushroom-risotto"), results)

    def test_recommended_for_subscription_uses_stored_preferences(self):
        make_meal(name="Peanut Bowl", slug="peanut-bowl", ingredients="peanuts, rice", goal=Goal.FAT_LOSS)
        safe = make_meal(name="Chicken Bowl", slug="chicken-bowl", ingredients="chicken, rice", goal=Goal.FAT_LOSS)
        subscription = Subscription(
            full_name="X", phone="0100", goal=Goal.FAT_LOSS, plan_type=PlanType.WEEKLY,
            start_date=timezone.localdate(), price=Decimal("1"), allergies="peanuts",
        )
        results = recommendation_service.recommended_for_subscription(subscription)
        self.assertIn(safe, results)


@override_settings(**TEST_SETTINGS)
class DeliveryClosedDateTests(TestCase):
    def setUp(self):
        for weekday in range(7):
            make_slot(weekday=weekday)

    def test_is_delivery_open_false_on_closed_date(self):
        date = timezone.localdate() + timedelta(days=5)
        self.assertTrue(is_delivery_open(date))
        DeliveryClosedDate.objects.create(date=date)
        self.assertFalse(is_delivery_open(date))

    def test_checkout_rejects_closed_delivery_date(self):
        meal = make_meal(price=Decimal("100.00"), daily_capacity=0)
        date, slot = next_bookable(DeliverySlot.objects.filter(is_active=True))
        DeliveryClosedDate.objects.create(date=date)
        self.client.post(reverse("cart_add_meal"), {"slug": meal.slug, "quantity": 1})
        payload = {
            "full_name": "Ahmed Hassan", "phone": "01000000020", "email": "",
            "governorate": "Cairo", "city": "Nasr City", "address": "1 test street building 1",
            "notes": "", "payment": "cash", "delivery_date": date.isoformat(), "delivery_slot": slot.pk,
            "coupon_code": "",
        }
        response = self.client.post(reverse("checkout"), payload)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not delivering")


@override_settings(**TEST_SETTINGS)
class MacroEstimateTests(TestCase):
    def test_estimate_macros_returns_all_targets(self):
        macros = calculator_service.estimate_macros(25, 180, 80, Goal.MUSCLE_GAIN)
        for key in ("calories", "protein_g", "carbs_g", "fat_g"):
            self.assertIn(key, macros)
            self.assertGreater(macros[key], 0)

    def test_fat_loss_protein_per_kg_higher_than_muscle_gain(self):
        loss = calculator_service.estimate_macros(25, 180, 80, Goal.FAT_LOSS)
        gain = calculator_service.estimate_macros(25, 180, 80, Goal.MUSCLE_GAIN)
        self.assertGreaterEqual(loss["protein_g"], gain["protein_g"])


@override_settings(**TEST_SETTINGS)
class AnalyticsServiceTests(TestCase):
    def setUp(self):
        for weekday in range(7):
            make_slot(weekday=weekday)

    def test_overview_runs_without_error_on_empty_data(self):
        from .services import analytics as analytics_service

        result = analytics_service.overview()
        self.assertEqual(result["order_count"], 0)
        self.assertIsNone(result["subscription_conversion_rate"])

    def test_best_selling_meals_reflects_real_order(self):
        from .services import analytics as analytics_service

        meal = make_meal(price=Decimal("50.00"), daily_capacity=0)
        date, slot = next_bookable(DeliverySlot.objects.filter(is_active=True))
        self.client.post(reverse("cart_add_meal"), {"slug": meal.slug, "quantity": 3})
        payload = {
            "full_name": "Ahmed Hassan", "phone": "01000000030", "email": "",
            "governorate": "Cairo", "city": "Nasr City", "address": "1 test street building 1",
            "notes": "", "payment": "cash", "delivery_date": date.isoformat(), "delivery_slot": slot.pk,
            "coupon_code": "",
        }
        self.client.post(reverse("checkout"), payload)
        best = list(analytics_service.best_selling_meals())
        self.assertEqual(best[0]["meal__name"], meal.name)
        self.assertEqual(best[0]["units_sold"], 3)
