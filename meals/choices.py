"""Choice enums shared across meals/models.py, forms, services and dashboard.

Kept in one place so Meal, Subscription and SubscriptionPlanPrice can never
drift out of sync on what a "goal" is.
"""

from django.db import models


class Goal(models.TextChoices):
    MUSCLE_GAIN = "muscle_gain", "Muscle Gain"
    FAT_LOSS = "fat_loss", "Fat Loss"
    MAINTAIN = "maintain", "Maintain Weight"


class MealType(models.TextChoices):
    BREAKFAST = "breakfast", "Breakfast"
    LUNCH = "lunch", "Lunch"
    DINNER = "dinner", "Dinner"


class PlanType(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"


# Calendar length backing each plan type. Used to compute a subscription's
# end_date and to generate its SubscriptionDelivery rows.
PLAN_DURATION_DAYS = {
    PlanType.WEEKLY: 7,
    PlanType.MONTHLY: 30,
}

MEALS_PER_DAY_CHOICES = [
    (1, "1 meal per day"),
    (2, "2 meals per day"),
    (3, "3 meals per day"),
]

WEEKDAY_CHOICES = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]
