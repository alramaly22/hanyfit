"""Server-side Calorie Calculator.

Deliberately backend-only (not duplicated in JS) so the number a customer is
quoted, and the meals/subscription recommended from it, always come from one
place and can be unit tested in isolation.

The product brief asks for Age / Height / Weight / Goal only -- no sex or
activity-level field -- so this uses the Mifflin-St Jeor BMR formula with a
sex-neutral constant (the midpoint between the male (+5) and female (-161)
terms) and a fixed "moderately active" multiplier. Both are settings
constants specifically so the numbers can be tuned later without a code
change. This is an estimate for meal planning, not medical or clinical
advice.
"""

from decimal import Decimal

from django.conf import settings

from ..choices import Goal
from . import recommendation as recommendation_service
from . import subscriptions as subscriptions_service

GOAL_CALORIE_ADJUSTMENT = {
    Goal.MUSCLE_GAIN: Decimal("1.15"),
    Goal.FAT_LOSS: Decimal("0.80"),
    Goal.MAINTAIN: Decimal("1.00"),
}

MIN_AGE, MAX_AGE = 10, 90
MIN_HEIGHT_CM, MAX_HEIGHT_CM = 100, 230
MIN_WEIGHT_KG, MAX_WEIGHT_KG = 30, 250


class CalculatorInputError(ValueError):
    """Raised when age/height/weight are outside a sane human range."""


def estimate_daily_calories(age, height_cm, weight_kg, goal):
    """Return the estimated daily calorie target (int) for these inputs."""
    age, height_cm, weight_kg = int(age), Decimal(height_cm), Decimal(weight_kg)

    if not (MIN_AGE <= age <= MAX_AGE):
        raise CalculatorInputError(f"Age must be between {MIN_AGE} and {MAX_AGE}.")
    if not (MIN_HEIGHT_CM <= height_cm <= MAX_HEIGHT_CM):
        raise CalculatorInputError(f"Height must be between {MIN_HEIGHT_CM} and {MAX_HEIGHT_CM} cm.")
    if not (MIN_WEIGHT_KG <= weight_kg <= MAX_WEIGHT_KG):
        raise CalculatorInputError(f"Weight must be between {MIN_WEIGHT_KG} and {MAX_WEIGHT_KG} kg.")
    if goal not in Goal.values:
        raise CalculatorInputError("Unknown goal.")

    activity_factor = Decimal(str(getattr(settings, "MEALS_CALCULATOR_ACTIVITY_FACTOR", "1.45")))
    neutral_constant = Decimal(str(getattr(settings, "MEALS_CALCULATOR_NEUTRAL_CONSTANT", "-78")))

    bmr = (Decimal("10") * weight_kg) + (Decimal("6.25") * height_cm) - (Decimal("5") * age) + neutral_constant
    tdee = bmr * activity_factor
    adjusted = tdee * GOAL_CALORIE_ADJUSTMENT.get(goal, Decimal("1.00"))

    # Round to the nearest 10 kcal -- a false-precision single-digit number
    # would overstate how exact this estimate is.
    return int((adjusted / 10).quantize(Decimal("1")) * 10)


# Grams of protein per kg of bodyweight. Fat loss sits slightly higher than
# muscle gain -- protecting muscle mass in a calorie deficit needs more
# protein per kg than a surplus does. Standard evidence-based ranges, not
# individually tuned; exposed as settings so they can be adjusted without a
# code change.
PROTEIN_G_PER_KG = {
    Goal.MUSCLE_GAIN: Decimal("2.0"),
    Goal.FAT_LOSS: Decimal("2.2"),
    Goal.MAINTAIN: Decimal("1.6"),
}
FAT_CALORIE_SHARE = Decimal("0.25")  # of total daily calories
KCAL_PER_G_PROTEIN = Decimal("4")
KCAL_PER_G_FAT = Decimal("9")
KCAL_PER_G_CARB = Decimal("4")


def estimate_macros(age, height_cm, weight_kg, goal):
    """Calories + protein/carb/fat targets (grams) for these inputs.

    Protein is set by bodyweight (see PROTEIN_G_PER_KG), fat as a fixed share
    of total calories, and carbs take whatever calories are left over. This
    is a standard estimate for meal planning, not an individualised or
    medical prescription -- see the disclaimer surfaced in meals/views.py
    and the calculator template.
    """
    calories = estimate_daily_calories(age, height_cm, weight_kg, goal)
    weight_kg = Decimal(weight_kg)

    protein_g = (weight_kg * PROTEIN_G_PER_KG.get(goal, Decimal("1.8"))).quantize(Decimal("1"))
    fat_kcal = Decimal(calories) * FAT_CALORIE_SHARE
    fat_g = (fat_kcal / KCAL_PER_G_FAT).quantize(Decimal("1"))
    remaining_kcal = Decimal(calories) - (protein_g * KCAL_PER_G_PROTEIN) - fat_kcal
    carbs_g = max((remaining_kcal / KCAL_PER_G_CARB).quantize(Decimal("1")), Decimal("0"))

    return {
        "calories": calories,
        "protein_g": int(protein_g),
        "carbs_g": int(carbs_g),
        "fat_g": int(fat_g),
    }


def recommend_meals(goal, daily_calories, protein_target=None, limit=6):
    """Thin wrapper over services/recommendation.py -- kept here so existing
    callers (meals/views.py::calculator) don't need to know the calculator
    and the general recommendation engine are two different modules."""
    return recommendation_service.recommend_meals(
        goal, calories_target=daily_calories, protein_target=protein_target, limit=limit,
    )


def recommend_subscription_plans(goal, meals_per_day=1):
    """Priced subscription options for this goal, for every plan type that
    currently has a configured price.
    """
    plans = []
    for plan_type, label in (("weekly", "Weekly"), ("monthly", "Monthly")):
        try:
            price = subscriptions_service.calculate_price(plan_type, goal, meals_per_day)
        except subscriptions_service.SubscriptionError:
            continue
        plans.append({
            "plan_type": plan_type,
            "plan_label": label,
            "goal": goal,
            "meals_per_day": meals_per_day,
            "price": price,
        })
    return plans
