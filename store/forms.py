"""Checkout form.

The previous checkout read straight from ``request.POST`` with no validation,
so a blank name or a nonsense phone number produced an unusable order that
nobody could deliver. Everything is validated here instead, and the form is
re-rendered with errors and the customer's input intact on failure.
"""

import re
from datetime import timedelta

from django import forms
from django.conf import settings
from django.utils import timezone

from . import geo
from .models import Order
from meals.services import availability as meal_availability

# Egypt's 27 governorates. A select rather than a free text field: typo-free
# values make delivery zones and shipping reports possible later.
GOVERNORATES = [
    "Cairo",
    "Giza",
    "Alexandria",
    "Dakahlia",
    "Red Sea",
    "Beheira",
    "Fayoum",
    "Gharbia",
    "Ismailia",
    "Menofia",
    "Minya",
    "Qalyubia",
    "New Valley",
    "Suez",
    "Aswan",
    "Assiut",
    "Beni Suef",
    "Port Said",
    "Damietta",
    "Sharkia",
    "South Sinai",
    "Kafr El Sheikh",
    "Matrouh",
    "Luxor",
    "Qena",
    "North Sinai",
    "Sohag",
]

# Accepts 01XXXXXXXXX, 201XXXXXXXXX, +201XXXXXXXXX and also allows other
# international numbers so customers outside Egypt are not locked out.
EGYPT_MOBILE = re.compile(r"^(?:\+?20)?1[0125]\d{8}$")
GENERIC_INTERNATIONAL = re.compile(r"^\+?\d{8,15}$")


class CheckoutForm(forms.ModelForm):
    payment = forms.ChoiceField(
        choices=Order.PaymentMethod.choices,
        initial=Order.PaymentMethod.CASH,
        widget=forms.RadioSelect,
        error_messages={"required": "Choose how you would like to pay."},
    )
    # Only meaningful (and only required) when the cart contains a HanyFit
    # Meals line -- see ``requires_delivery`` below. Left optional at the
    # model level (Order.delivery_date/.delivery_slot are null=True) since
    # apparel-only orders never set them.
    coupon_code = forms.CharField(
        required=False,
        max_length=40,
        widget=forms.TextInput(attrs={"placeholder": "Coupon code (optional)"}),
    )

    class Meta:
        model = Order
        fields = [
            "full_name",
            "phone",
            "email",
            "governorate",
            "city",
            "address",
            "notes",
            "delivery_date",
            "delivery_slot",
        ]
        widgets = {
            "full_name": forms.TextInput(
                attrs={"placeholder": "Your full name", "autocomplete": "name"}
            ),
            "phone": forms.TextInput(
                attrs={
                    "placeholder": "01xxxxxxxxx",
                    "inputmode": "tel",
                    "autocomplete": "tel",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "you@example.com",
                    "autocomplete": "email",
                }
            ),
            "governorate": forms.Select(
                choices=[("", "Select your governorate")]
                + [(g, g) for g in GOVERNORATES]
            ),
            "city": forms.TextInput(
                attrs={"placeholder": "City or district", "autocomplete": "address-level2"}
            ),
            "address": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Street, building, floor, apartment",
                    "autocomplete": "street-address",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Landmark, preferred delivery time, anything else",
                }
            ),
            "delivery_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "full_name": "Full name",
            "phone": "Phone number",
            "email": "Email",
            "governorate": "Governorate",
            "city": "City",
            "address": "Address",
            "notes": "Delivery notes",
            "delivery_date": "Delivery date",
            "delivery_slot": "Delivery time",
        }
        error_messages = {
            "full_name": {"required": "We need a name for the delivery."},
            "phone": {"required": "We need a phone number to arrange delivery."},
            "governorate": {"required": "Select your governorate."},
            "city": {"required": "Enter your city."},
            "address": {"required": "Enter the full address."},
        }

    def __init__(self, *args, country=None, requires_delivery=False, **kwargs):
        super().__init__(*args, **kwargs)
        # Which country this checkout is for -- passed in by the view from
        # the visitor's session (store/geo.py), never taken from POST data,
        # so a customer cannot submit "SA" to dodge the governorate field
        # while still being priced/shipped as Egypt, or vice versa.
        self.country = country or settings.STORE_DEFAULT_COUNTRY
        self.has_regions = geo.has_regions(self.country)
        # True when the cart contains at least one HanyFit Meals line --
        # passed in by the view (store/views.py::checkout), never inferred
        # from POST, so a delivery date can't be smuggled onto an apparel-only
        # order or skipped on a meal order.
        self.requires_delivery = requires_delivery

        # Email is optional on the model, but online payment needs a receipt
        # address, so it becomes conditionally required in clean().
        self.fields["email"].required = False
        self.fields["notes"].required = False

        from meals.models import DeliverySlot

        self.fields["delivery_slot"].queryset = DeliverySlot.objects.filter(is_active=True)
        self.fields["delivery_date"].required = self.requires_delivery
        self.fields["delivery_slot"].required = self.requires_delivery
        if not self.requires_delivery:
            self.fields["delivery_date"].widget = forms.HiddenInput()
            self.fields["delivery_slot"].widget = forms.HiddenInput()

        if not self.has_regions:
            # Saudi Arabia (and any future country without a region list):
            # no governorate dropdown, just a free-form city + address.
            self.fields["governorate"].required = False
            self.fields["governorate"].widget = forms.HiddenInput()
            self.fields["address"].widget.attrs["placeholder"] = (
                "City, district, street, building and apartment number"
            )

    def clean_full_name(self):
        name = " ".join((self.cleaned_data.get("full_name") or "").split())
        if len(name) < 3:
            raise forms.ValidationError("Enter your full name.")
        if not re.search(r"[^\W\d_]", name, flags=re.UNICODE):
            raise forms.ValidationError("Enter your name in letters.")
        return name

    def clean_phone(self):
        raw = (self.cleaned_data.get("phone") or "").strip()
        compact = re.sub(r"[\s\-()]", "", raw)

        if EGYPT_MOBILE.match(compact):
            digits = re.sub(r"\D", "", compact)
            # Store one canonical local format so staff see consistent numbers.
            if digits.startswith("20"):
                digits = digits[2:]
            return f"0{digits}" if not digits.startswith("0") else digits

        if GENERIC_INTERNATIONAL.match(compact):
            return compact

        raise forms.ValidationError(
            "Enter a valid mobile number, for example 01012345678."
        )

    def clean_city(self):
        city = " ".join((self.cleaned_data.get("city") or "").split())
        if len(city) < 2:
            raise forms.ValidationError("Enter your city.")
        return city

    def clean_address(self):
        address = " ".join((self.cleaned_data.get("address") or "").split())
        if len(address) < 10:
            raise forms.ValidationError(
                "Add a little more detail so the courier can find you."
            )
        return address

    def clean_governorate(self):
        governorate = (self.cleaned_data.get("governorate") or "").strip()
        if not self.has_regions:
            # Saudi Arabia etc: not asked for, not validated, not stored.
            return ""
        if governorate not in GOVERNORATES:
            raise forms.ValidationError("Select your governorate from the list.")
        return governorate

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment") == Order.PaymentMethod.ONLINE and not cleaned.get(
            "email"
        ):
            self.add_error(
                "email",
                "Add your email so the payment provider can send your receipt.",
            )

        if self.requires_delivery:
            delivery_date = cleaned.get("delivery_date")
            delivery_slot = cleaned.get("delivery_slot")
            if delivery_date:
                today = timezone.localdate()
                window = getattr(settings, "MEALS_DELIVERY_WINDOW_DAYS", 14)
                if delivery_date < today:
                    self.add_error("delivery_date", "Choose a delivery date in the future.")
                elif delivery_date > today + timedelta(days=window):
                    self.add_error(
                        "delivery_date",
                        f"Delivery dates are only open {window} days ahead.",
                    )
                elif not meal_availability.is_delivery_open(delivery_date):
                    self.add_error(
                        "delivery_date",
                        "We're not delivering on that date. Please choose another.",
                    )
                elif delivery_slot and not delivery_slot.is_bookable_for(delivery_date):
                    self.add_error(
                        "delivery_slot",
                        "That time slot is not available on the chosen date.",
                    )
        return cleaned
