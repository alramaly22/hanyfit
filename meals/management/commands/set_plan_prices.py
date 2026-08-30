"""Set (or update) the HanyFit Meals subscription plan prices only.

Safer than seed_meals for running against a live/production database:
seed_meals also creates/updates Meal and DeliverySlot rows with demo data,
which could overwrite real meal names/prices if a slug happens to match.
This command touches SubscriptionPlanPrice and nothing else.

Usage (with your production DATABASE_URL set in the environment):
    python manage.py set_plan_prices
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from meals.choices import Goal, PlanType
from meals.models import SubscriptionPlanPrice

# Same price across all three goals (Muscle Gain / Fat Loss / Maintain) --
# confirmed explicitly, not a placeholder.
PLAN_PRICES = [
    # Weekly
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 1, "1299.00"),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 1, "1299.00"),
    (PlanType.WEEKLY, Goal.MAINTAIN, 1, "1299.00"),
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 2, "2299.00"),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 2, "2299.00"),
    (PlanType.WEEKLY, Goal.MAINTAIN, 2, "2299.00"),
    # Weekly, 3 meals/day -- kept but inactive by default (see
    # INACTIVE_BY_DEFAULT below). Flip is_active in the admin to turn it on.
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 3, "4000.00"),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 3, "4000.00"),
    (PlanType.WEEKLY, Goal.MAINTAIN, 3, "4000.00"),
    # Monthly -- no 3-meals-a-day option at all in this phase.
    (PlanType.MONTHLY, Goal.MUSCLE_GAIN, 1, "4999.00"),
    (PlanType.MONTHLY, Goal.FAT_LOSS, 1, "4999.00"),
    (PlanType.MONTHLY, Goal.MAINTAIN, 1, "4999.00"),
    (PlanType.MONTHLY, Goal.MUSCLE_GAIN, 2, "8499.00"),
    (PlanType.MONTHLY, Goal.FAT_LOSS, 2, "8499.00"),
    (PlanType.MONTHLY, Goal.MAINTAIN, 2, "8499.00"),
]

INACTIVE_BY_DEFAULT = {
    (PlanType.WEEKLY, Goal.MUSCLE_GAIN, 3),
    (PlanType.WEEKLY, Goal.FAT_LOSS, 3),
    (PlanType.WEEKLY, Goal.MAINTAIN, 3),
}


class Command(BaseCommand):
    help = "Set the HanyFit Meals weekly/monthly subscription prices. Only touches SubscriptionPlanPrice."

    @transaction.atomic
    def handle(self, *args, **options):
        for plan_type, goal, meals_per_day, price in PLAN_PRICES:
            is_active = (plan_type, goal, meals_per_day) not in INACTIVE_BY_DEFAULT
            plan, created = SubscriptionPlanPrice.objects.update_or_create(
                plan_type=plan_type,
                goal=goal,
                meals_per_day=meals_per_day,
                defaults={"price": Decimal(price), "is_active": is_active},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if created else 'Updated'}: {plan} "
                    f"-> {price} EGP ({'active' if is_active else 'inactive'})"
                )
            )

        # Any monthly, 3-meals-a-day row left over from an earlier
        # configuration is explicitly not offered in this phase.
        removed = SubscriptionPlanPrice.objects.filter(
            plan_type=PlanType.MONTHLY, meals_per_day=3
        )
        count = removed.count()
        if count:
            removed.delete()
            self.stdout.write(
                self.style.WARNING(f"Removed {count} monthly/3-meals-a-day row(s) -- not offered.")
            )

        self.stdout.write(self.style.SUCCESS("Done."))
