"""Forms for the customer-facing HanyFit Meals pages.

Phone/address validation is intentionally *imported* from store/forms.py
(EGYPT_MOBILE, GENERIC_INTERNATIONAL, GOVERNORATES) rather than duplicated --
a subscription's delivery address needs exactly the same validation an order's
does, and the checkout form's rules are the ones that have already been
tuned against real phone numbers.
"""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.utils import timezone

from store.forms import EGYPT_MOBILE, GENERIC_INTERNATIONAL, GOVERNORATES
import re

from .choices import Goal, MEALS_PER_DAY_CHOICES, WEEKDAY_CHOICES
from .models import DeliverySlot, Subscription, SubscriptionPlanPrice


class TimeWindowChoiceField(forms.ModelChoiceField):
    """Displays a DeliverySlot as just its time range ("13:00 - 15:00"),
    with the weekday dropped -- see SubscriptionForm.delivery_slot."""

    def label_from_instance(self, obj):
        return f"{obj.start_time:%H:%M} - {obj.end_time:%H:%M}"


class CalculatorForm(forms.Form):
    age = forms.IntegerField(min_value=10, max_value=90, widget=forms.NumberInput(attrs={"placeholder": "Age"}))
    height_cm = forms.IntegerField(
        min_value=100, max_value=230, label="Height (cm)",
        widget=forms.NumberInput(attrs={"placeholder": "Height in cm"}),
    )
    weight_kg = forms.IntegerField(
        min_value=30, max_value=250, label="Weight (kg)",
        widget=forms.NumberInput(attrs={"placeholder": "Weight in kg"}),
    )
    goal = forms.ChoiceField(choices=Goal.choices, widget=forms.RadioSelect)


class SubscriptionForm(forms.Form):
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"placeholder": "Your full name"}))
    phone = forms.CharField(max_length=30, widget=forms.TextInput(attrs={"placeholder": "01xxxxxxxxx", "inputmode": "tel"}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"placeholder": "you@example.com"}))

    goal = forms.ChoiceField(choices=Goal.choices, widget=forms.RadioSelect)
    meals_per_day = forms.TypedChoiceField(
        choices=MEALS_PER_DAY_CHOICES, coerce=int, required=False, widget=forms.RadioSelect,
        label="Meals per day",
    )

    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    # A Subscription delivers every day, so "delivery_slot" here means "which
    # time-of-day window do you want, every day" -- not "which single weekday
    # slot". DeliverySlot rows are still weekday+time (they were designed for
    # one-off meal-order checkout, see store/views.py::checkout), so this
    # field is restricted in __init__ to one representative row per distinct
    # (start_time, end_time) pair -- the weekday on that particular row is
    # irrelevant for a subscription and is never checked in clean().
    delivery_slot = TimeWindowChoiceField(
        queryset=DeliverySlot.objects.none(),
        required=False,
        label="Preferred delivery time (applies every day)",
    )
    delivery_weekdays = forms.TypedMultipleChoiceField(
        choices=WEEKDAY_CHOICES, coerce=int, required=False, widget=forms.CheckboxSelectMultiple,
        label="Delivery days (leave all unchecked for every day)",
    )

    allergies = forms.CharField(
        required=False, label="Allergies",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. peanuts, shellfish"}),
    )
    disliked_foods = forms.CharField(
        required=False, label="Foods you'd rather skip",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. mushrooms, olives"}),
    )
    dietary_notes = forms.CharField(
        required=False, label="Any other dietary notes",
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    governorate = forms.ChoiceField(
        choices=[("", "Select your governorate")] + [(g, g) for g in GOVERNORATES],
    )
    city = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"placeholder": "City or district"}))
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Street, building, floor, apartment"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Anything else"}))

    payment_method = forms.ChoiceField(
        choices=Subscription.PaymentMethod.choices,
        initial=Subscription.PaymentMethod.CASH,
        widget=forms.RadioSelect,
    )
    coupon_code = forms.CharField(required=False, max_length=40, widget=forms.TextInput(attrs={"placeholder": "Coupon code (optional)"}))

    def __init__(self, *args, plan_type="weekly", **kwargs):
        super().__init__(*args, **kwargs)
        self.plan_type = plan_type
        # The set of "meals per day" options offered is read straight from
        # SubscriptionPlanPrice (is_active=True) for this plan_type -- not
        # hardcoded. To add or remove an option (e.g. stop offering 3
        # meals/day, or start offering 2/day for the monthly plan), toggle
        # or add a SubscriptionPlanPrice row in the admin; nothing here
        # needs to change. If only one option ends up active, the field is
        # pinned to it and hidden, matching the old "monthly is fixed at 1
        # meal/day" behaviour as a special case of the general rule rather
        # than a hardcoded exception.
        available = self._available_meals_per_day(plan_type)
        self.fields["meals_per_day"].choices = [
            (n, label) for n, label in MEALS_PER_DAY_CHOICES if n in available
        ]
        if len(available) <= 1:
            self.fields["meals_per_day"].required = False
            self.fields["meals_per_day"].widget = forms.HiddenInput()
            self.initial.setdefault("meals_per_day", next(iter(available), 1))
        else:
            self.fields["meals_per_day"].required = True
        self.fields["delivery_slot"].queryset = self._time_window_choices()

    @staticmethod
    def _available_meals_per_day(plan_type):
        values = set(
            SubscriptionPlanPrice.objects.filter(plan_type=plan_type, is_active=True)
            .values_list("meals_per_day", flat=True)
        )
        return values or {1}

    @staticmethod
    def _time_window_choices():
        """One DeliverySlot row per distinct (start_time, end_time) pair.

        DeliverySlot rows are weekday + time (seed_meals creates 7 x 2 =
        14 of them), because that is what a one-off meal order's checkout
        needs (store/views.py::checkout). A subscription only needs the time
        part -- "afternoon" or "evening", applied every day -- so this picks
        one representative row per distinct time window instead of showing
        all 14 (which is what produced the confusing "Monday 13:00-15:00" /
        "Tuesday 13:00-15:00" / ... list, and the mismatched-weekday error).
        Portable across DB backends -- no DISTINCT ON, which SQLite lacks.
        """
        seen = set()
        ids = []
        for slot in DeliverySlot.objects.filter(is_active=True).order_by("start_time", "weekday"):
            key = (slot.start_time, slot.end_time)
            if key in seen:
                continue
            seen.add(key)
            ids.append(slot.pk)
        return DeliverySlot.objects.filter(pk__in=ids).order_by("start_time")

    def clean_full_name(self):
        name = " ".join((self.cleaned_data.get("full_name") or "").split())
        if len(name) < 3:
            raise forms.ValidationError("Enter your full name.")
        return name

    def clean_phone(self):
        raw = (self.cleaned_data.get("phone") or "").strip()
        compact = re.sub(r"[\s\-()]", "", raw)
        if EGYPT_MOBILE.match(compact):
            digits = re.sub(r"\D", "", compact)
            if digits.startswith("20"):
                digits = digits[2:]
            return f"0{digits}" if not digits.startswith("0") else digits
        if GENERIC_INTERNATIONAL.match(compact):
            return compact
        raise forms.ValidationError("Enter a valid mobile number, for example 01012345678.")

    def clean_address(self):
        address = " ".join((self.cleaned_data.get("address") or "").split())
        if len(address) < 10:
            raise forms.ValidationError("Add a little more detail so the courier can find you.")
        return address

    def clean_start_date(self):
        start_date = self.cleaned_data.get("start_date")
        if start_date:
            today = timezone.localdate()
            window = getattr(settings, "MEALS_DELIVERY_WINDOW_DAYS", 14)
            if start_date < today:
                raise forms.ValidationError("Choose a start date in the future.")
            if start_date > today + timedelta(days=window):
                raise forms.ValidationError(f"Start dates are only open {window} days ahead.")
        return start_date

    def clean(self):
        cleaned = super().clean()

        # If meals_per_day ended up hidden (single-option plan), its value
        # never arrives in POST data -- fall back to the one available
        # option rather than failing validation on a field the customer
        # never saw.
        if not cleaned.get("meals_per_day"):
            available = self._available_meals_per_day(self.plan_type)
            cleaned["meals_per_day"] = next(iter(available), 1)

        # No weekday check here on purpose: a subscription delivers every
        # day, so delivery_slot only expresses a time-of-day preference (see
        # _time_window_choices above), never a single bookable weekday+date
        # combination the way the one-off checkout's delivery_slot does.

        if cleaned.get("payment_method") == Subscription.PaymentMethod.ONLINE and not cleaned.get("email"):
            self.add_error("email", "Add your email so the payment provider can send your receipt.")

        return cleaned


class AddressChangeForm(forms.Form):
    """Used from the subscription self-service page -- see
    meals/views.py::subscription_change_address."""

    governorate = forms.ChoiceField(choices=[("", "Select your governorate")] + [(g, g) for g in GOVERNORATES])
    city = forms.CharField(max_length=100)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def clean_address(self):
        address = " ".join((self.cleaned_data.get("address") or "").split())
        if len(address) < 10:
            raise forms.ValidationError("Add a little more detail so the courier can find you.")
        return address


class RescheduleDeliveryForm(forms.Form):
    """Used from the subscription self-service page -- see
    meals/views.py::subscription_reschedule_delivery."""

    new_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean_new_date(self):
        new_date = self.cleaned_data.get("new_date")
        if new_date and new_date <= timezone.localdate():
            raise forms.ValidationError("Choose a date in the future.")
        return new_date


class ReviewForm(forms.Form):
    """Used from the guest order-tracking page -- see
    meals/views.py::add_review."""

    rating = forms.TypedChoiceField(
        choices=[(n, str(n)) for n in range(1, 6)], coerce=int, widget=forms.RadioSelect,
    )
    comment = forms.CharField(
        required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional comment"}),
    )
