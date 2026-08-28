"""Seed HanyFit Meals with the initial catalogue from the product brief.

    python manage.py seed_meals
    python manage.py seed_meals --reset

Idempotent (update_or_create keyed by slug/combo), like seed_store.py. Prices
and macros here are demo values for testing -- edit them for real in the
Django admin (Meal, SubscriptionPlanPrice) once real numbers are ready.
"""

from datetime import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from meals.choices import Goal, MealType, PlanType
from meals.models import DeliverySlot, Meal, SubscriptionPlanPrice

MEALS = [
    # ---- Muscle Gain -----------------------------------------------------
    {
        "name": "Chicken + Rice + Potatoes",
        "goal": Goal.MUSCLE_GAIN,
        "meal_type": MealType.LUNCH,
        "price": "140.00",
        "calories": 650,
        "protein_g": "48.0",
        "carbs_g": "70.0",
        "fat_g": "14.0",
        "ingredients": "Grilled chicken breast, jasmine rice, roasted potatoes, olive oil.",
    },
    {
        "name": "Chicken Pasta",
        "goal": Goal.MUSCLE_GAIN,
        "meal_type": MealType.DINNER,
        "price": "135.00",
        "calories": 620,
        "protein_g": "42.0",
        "carbs_g": "68.0",
        "fat_g": "16.0",
        "ingredients": "Chicken breast, whole-wheat pasta, tomato sauce, parmesan.",
    },
    {
        "name": "Steak + Rice",
        "goal": Goal.MUSCLE_GAIN,
        "meal_type": MealType.DINNER,
        "price": "165.00",
        "calories": 700,
        "protein_g": "50.0",
        "carbs_g": "60.0",
        "fat_g": "22.0",
        "ingredients": "Grilled beef steak, jasmine rice, sauteed vegetables.",
    },
    {
        "name": "Healthy Burger",
        "goal": Goal.MUSCLE_GAIN,
        "meal_type": MealType.LUNCH,
        "price": "150.00",
        "calories": 680,
        "protein_g": "40.0",
        "carbs_g": "65.0",
        "fat_g": "24.0",
        "ingredients": "Lean beef patty, whole-wheat bun, cheddar, lettuce, tomato, sweet potato fries.",
    },
    {
        "name": "Protein Pancakes",
        "goal": Goal.MUSCLE_GAIN,
        "meal_type": MealType.BREAKFAST,
        "price": "110.00",
        "calories": 520,
        "protein_g": "35.0",
        "carbs_g": "55.0",
        "fat_g": "12.0",
        "ingredients": "Oats, whey protein, eggs, banana, honey.",
    },
    # ---- Fat Loss ----------------------------------------------------------
    {
        "name": "Grilled Chicken + Vegetables",
        "goal": Goal.FAT_LOSS,
        "meal_type": MealType.LUNCH,
        "price": "120.00",
        "calories": 420,
        "protein_g": "42.0",
        "carbs_g": "25.0",
        "fat_g": "12.0",
        "ingredients": "Grilled chicken breast, broccoli, zucchini, bell peppers.",
    },
    {
        "name": "Salmon + Vegetables",
        "goal": Goal.FAT_LOSS,
        "meal_type": MealType.DINNER,
        "price": "160.00",
        "calories": 460,
        "protein_g": "38.0",
        "carbs_g": "20.0",
        "fat_g": "22.0",
        "ingredients": "Grilled salmon, asparagus, cherry tomatoes, lemon.",
    },
    {
        "name": "Tuna + Brown Rice",
        "goal": Goal.FAT_LOSS,
        "meal_type": MealType.LUNCH,
        "price": "115.00",
        "calories": 430,
        "protein_g": "36.0",
        "carbs_g": "45.0",
        "fat_g": "8.0",
        "ingredients": "Tuna, brown rice, cucumber, lemon dressing.",
    },
    {
        "name": "Protein Salad",
        "goal": Goal.FAT_LOSS,
        "meal_type": MealType.LUNCH,
        "price": "105.00",
        "calories": 380,
        "protein_g": "32.0",
        "carbs_g": "22.0",
        "fat_g": "14.0",
        "ingredients": "Grilled chicken, mixed greens, chickpeas, feta, olive oil dressing.",
    },
    {
        "name": "Egg White Omelet",
        "goal": Goal.FAT_LOSS,
        "meal_type": MealType.BREAKFAST,
        "price": "90.00",
        "calories": 320,
        "protein_g": "30.0",
        "carbs_g": "15.0",
        "fat_g": "10.0",
        "ingredients": "Egg whites, spinach, mushrooms, low-fat cheese.",
    },
    # ---- Maintain Weight -----------------------------------------------------
    {
        "name": "Balanced Meal",
        "goal": Goal.MAINTAIN,
        "meal_type": MealType.LUNCH,
        "price": "125.00",
        "calories": 550,
        "protein_g": "35.0",
        "carbs_g": "55.0",
        "fat_g": "18.0",
        "ingredients": "Grilled chicken, quinoa, roasted vegetables.",
    },
    {
        "name": "Healthy Sandwich",
        "goal": Goal.MAINTAIN,
        "meal_type": MealType.LUNCH,
        "price": "95.00",
        "calories": 480,
        "protein_g": "28.0",
        "carbs_g": "50.0",
        "fat_g": "16.0",
        "ingredients": "Whole-wheat bread, turkey breast, avocado, lettuce, tomato.",
    },
    {
        "name": "Oatmeal",
        "goal": Goal.MAINTAIN,
        "meal_type": MealType.BREAKFAST,
        "price": "70.00",
        "calories": 400,
        "protein_g": "16.0",
        "carbs_g": "60.0",
        "fat_g": "10.0",
        "ingredients": "Oats, milk, banana, honey, walnuts.",
    },
    {
        "name": "Protein Smoothie",
        "goal": Goal.MAINTAIN,
        "meal_type": MealType.BREAKFAST,
        "price": "85.00",
        "calories": 350,
        "protein_g": "25.0",
        "carbs_g": "45.0",
        "fat_g": "8.0",
        "ingredients": "Whey protein, banana, peanut butter, milk.",
    },
]

# Demo subscription pricing -- edit for real in the admin/dashboard.
PLAN_PRICES = [
    # Weekly -- real prices, given directly: 1 meal/day = 1299 EGP,
    # 2 meals/day = 2299 EGP. Same price across all three
    # goals -- adjust per-goal in the admin (Subscription plan prices) if
    # that ever needs to differ.
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 1, "1299.00"),
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 2, "2299.00"),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 1, "1299.00"),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 2, "2299.00"),
    (PlanType.WEEKLY, Goal.MAINTAIN, 1, "1299.00"),
    (PlanType.WEEKLY, Goal.MAINTAIN, 2, "2299.00"),
    # Weekly, 3 meals/day -- kept but INACTIVE by default. The "meals per
    # day" choices shown to customers are read live from whichever of these
    # rows have is_active=True (see SubscriptionForm._available_meals_per_day
    # in meals/forms.py) -- flip is_active on/off in the admin any time to
    # turn this option on or off, no code change needed.
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 3, "4000.00"),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 3, "4000.00"),
    (PlanType.WEEKLY, Goal.MAINTAIN, 3, "4000.00"),

    # Monthly -- 1 meal/day = 4999 EGP, 2 meals/day = 8499 EGP.
    (PlanType.MONTHLY, Goal.MUSCLE_GAIN, 1, "4999.00"),
    (PlanType.MONTHLY, Goal.FAT_LOSS, 1, "4999.00"),
    (PlanType.MONTHLY, Goal.MAINTAIN, 1, "4999.00"),
    (PlanType.MONTHLY, Goal.MUSCLE_GAIN, 2, "8499.00"),
    (PlanType.MONTHLY, Goal.FAT_LOSS, 2, "8499.00"),
    (PlanType.MONTHLY, Goal.MAINTAIN, 2, "8499.00"),
]

# Rows here are seeded is_active=True by default except these -- see the
# comment above the weekly-3-meals rows.
INACTIVE_BY_DEFAULT = {
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 3),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 3),
    (PlanType.WEEKLY, Goal.MAINTAIN, 3),
}

# A simple default delivery window: lunch and dinner slots, every day.
DELIVERY_SLOTS = [
    (weekday, time(13, 0), time(15, 0))
    for weekday in range(7)
] + [
    (weekday, time(18, 0), time(20, 0))
    for weekday in range(7)
]


class Command(BaseCommand):
    help = "Create the initial HanyFit Meals catalogue, plan prices and delivery slots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing meals/plan prices/delivery slots first.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            Meal.objects.all().delete()
            SubscriptionPlanPrice.objects.all().delete()
            DeliverySlot.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared existing meals, plan prices and delivery slots."))

        for order, spec in enumerate(MEALS, start=1):
            slug = slugify(spec["name"])
            meal, created = Meal.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": spec["name"],
                    "goal": spec["goal"],
                    "meal_type": spec["meal_type"],
                    "price": Decimal(spec["price"]),
                    "calories": spec["calories"],
                    "protein_g": Decimal(spec["protein_g"]),
                    "carbs_g": Decimal(spec["carbs_g"]),
                    "fat_g": Decimal(spec["fat_g"]),
                    "ingredients": spec["ingredients"],
                    "description": spec["ingredients"],
                    "daily_capacity": 50,
                    "display_order": order,
                    "is_active": True,
                },
            )
            self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} meal: {meal.name}"))

        for plan_type, goal, meals_per_day, price in PLAN_PRICES:
            is_active = (plan_type, goal, meals_per_day) not in INACTIVE_BY_DEFAULT
            plan, created = SubscriptionPlanPrice.objects.update_or_create(
                plan_type=plan_type, goal=goal, meals_per_day=meals_per_day,
                defaults={"price": Decimal(price), "is_active": is_active},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if created else 'Updated'} plan price: {plan} ({'active' if is_active else 'inactive'})"
                )
            )

        for weekday, start, end in DELIVERY_SLOTS:
            slot, created = DeliverySlot.objects.update_or_create(
                weekday=weekday, start_time=start, end_time=end,
                defaults={"is_active": True, "cutoff_hours_before": 4},
            )

        self.stdout.write(self.style.SUCCESS(f"Ensured {len(DELIVERY_SLOTS)} delivery slots."))
        self.stdout.write("")
        self.stdout.write(
            "Meal images are blank by default -- add a static path or full https:// "
            "URL to each Meal in the admin (or bulk-update Meal.image)."
        )
