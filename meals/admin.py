from django.contrib import admin

from .models import (
    Coupon,
    CouponRedemption,
    DeliveryClosedDate,
    DeliverySlot,
    Meal,
    MealDailyAvailability,
    Review,
    Subscription,
    SubscriptionDelivery,
    SubscriptionPlanPrice,
)


@admin.register(Meal)
class MealAdmin(admin.ModelAdmin):
    list_display = ("name", "goal", "meal_type", "price", "calories", "protein_g", "daily_capacity", "average_rating", "is_active")
    list_filter = ("goal", "meal_type", "is_active")
    search_fields = ("name", "description", "ingredients")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("price", "is_active", "daily_capacity")

    @admin.display(description="Rating")
    def average_rating(self, obj):
        avg = obj.average_rating
        return f"{avg:.1f} ({obj.review_count})" if avg is not None else "\u2014"


@admin.register(MealDailyAvailability)
class MealDailyAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("meal", "date", "capacity_override", "reserved", "is_closed")
    list_filter = ("is_closed", "date")
    search_fields = ("meal__name",)
    autocomplete_fields = ["meal"]


@admin.register(DeliverySlot)
class DeliverySlotAdmin(admin.ModelAdmin):
    list_display = ("__str__", "weekday", "start_time", "end_time", "cutoff_hours_before", "is_active")
    list_filter = ("weekday", "is_active")
    list_editable = ("is_active",)


@admin.register(DeliveryClosedDate)
class DeliveryClosedDateAdmin(admin.ModelAdmin):
    """Close an entire date to *all* deliveries -- meal orders and
    subscriptions alike (public holiday, kitchen shut, etc). Distinct from
    MealDailyAvailability, which closes one specific meal on one date."""
    list_display = ("date", "reason", "created_at")
    ordering = ("date",)


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "discount_value", "applies_to", "is_active", "times_used", "usage_limit", "expires_at")
    list_filter = ("discount_type", "applies_to", "is_active")
    search_fields = ("code",)
    readonly_fields = ("times_used",)


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ("coupon", "phone", "discount_amount", "order", "subscription", "created_at")
    search_fields = ("phone", "coupon__code")
    readonly_fields = [f.name for f in CouponRedemption._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(SubscriptionPlanPrice)
class SubscriptionPlanPriceAdmin(admin.ModelAdmin):
    list_display = ("plan_type", "goal", "meals_per_day", "price", "is_active")
    list_filter = ("plan_type", "goal", "is_active")
    list_editable = ("price", "is_active")


class SubscriptionDeliveryInline(admin.TabularInline):
    model = SubscriptionDelivery
    extra = 0
    fields = ("scheduled_date", "status", "notes")
    readonly_fields = ("scheduled_date",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "full_name", "phone", "plan_type", "goal", "meals_per_day", "status",
        "start_date", "end_date", "total_price", "has_preferences",
    )
    list_filter = ("plan_type", "goal", "status")
    search_fields = ("full_name", "phone", "email", "allergies", "disliked_foods")
    readonly_fields = ("access_token", "created_at", "updated_at", "renewed_from")
    inlines = [SubscriptionDeliveryInline]
    fieldsets = (
        (None, {"fields": ("full_name", "phone", "email", "status")}),
        ("Plan", {"fields": ("plan_type", "goal", "meals_per_day", "delivery_weekdays", "start_date", "end_date", "delivery_slot")}),
        ("Nutrition preferences", {"fields": ("allergies", "disliked_foods", "dietary_notes")}),
        ("Delivery address", {"fields": ("governorate", "city", "address", "notes")}),
        ("Payment", {"fields": ("payment_method", "payment_status", "price", "discount_amount", "coupon", "paid_at")}),
        ("Meta", {"fields": ("renewed_from", "access_token", "created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description="Has preferences")
    def has_preferences(self, obj):
        return bool(obj.allergies or obj.disliked_foods or obj.dietary_notes)


@admin.register(SubscriptionDelivery)
class SubscriptionDeliveryAdmin(admin.ModelAdmin):
    list_display = ("subscription", "scheduled_date", "status")
    list_filter = ("status", "scheduled_date")
    autocomplete_fields = ["subscription"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("meal", "rating", "order_item", "is_hidden", "created_at")
    list_filter = ("rating", "is_hidden")
    search_fields = ("meal__name", "comment")
    list_editable = ("is_hidden",)
    readonly_fields = ("order_item", "meal", "created_at")
