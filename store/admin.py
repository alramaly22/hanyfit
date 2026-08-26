"""Admin configuration, arranged around what the shop owner does daily:
read new orders, mark them shipped, and keep stock counts correct.
"""

from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Order, OrderItem, Product, ProductVariant, WebhookEvent


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("size", "stock", "is_active")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "thumbnail",
        "name",
        "price",
        "total_stock",
        "is_active",
        "is_featured",
        "display_order",
    )
    list_display_links = ("thumbnail", "name")
    list_filter = ("is_active", "is_featured")
    list_editable = ("price", "is_active", "is_featured", "display_order")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductVariantInline]
    readonly_fields = ("created_at", "updated_at", "image_preview")
    fieldsets = (
        (None, {"fields": ("name", "slug", "description")}),
        ("Pricing", {"fields": ("price", "compare_at_price")}),
        (
            "Images",
            {
                "fields": ("front_image", "back_image", "image_preview"),
                "description": (
                    "Use a path inside static (images/store/tee-front.png) or a "
                    "full https:// URL. Vercel cannot store uploaded files, so "
                    "images must be committed to the repo or hosted on a CDN."
                ),
            },
        ),
        ("Visibility", {"fields": ("is_active", "is_featured", "display_order")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("variants")

    @admin.display(description="")
    def thumbnail(self, obj):
        url = obj.front_image_url
        if not url:
            return "—"
        return format_html(
            '<img src="{}" style="height:46px;width:38px;object-fit:cover;'
            'border-radius:4px;" alt="">',
            url,
        )

    @admin.display(description="Preview")
    def image_preview(self, obj):
        parts = [url for url in (obj.front_image_url, obj.back_image_url) if url]
        if not parts:
            return "No images set."
        return format_html(
            "".join(
                '<img src="{}" style="height:150px;margin-right:10px;'
                'border-radius:6px;" alt="">'
                for _ in parts
            ),
            *parts,
        )

    @admin.display(description="Stock")
    def total_stock(self, obj):
        total = sum(v.stock for v in obj.variants.all())
        if total == 0:
            return format_html('<span style="color:#c0392b;">Sold out</span>')
        if total <= 5:
            return format_html('<span style="color:#d68910;">{} left</span>', total)
        return total


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product_name", "size", "quantity", "unit_price", "line_total_display")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Line total")
    def line_total_display(self, obj):
        return obj.line_total


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "created_at",
        "full_name",
        "phone",
        "governorate",
        "payment_method",
        "payment_badge",
        "order_status",
        "total_price",
    )
    list_display_links = ("order_number",)
    list_filter = (
        "payment_status",
        "order_status",
        "payment_method",
        "governorate",
        "created_at",
    )
    list_editable = ("order_status",)
    search_fields = (
        "order_number",
        "full_name",
        "phone",
        "email",
        "fawaterk_invoice_id",
    )
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]
    list_per_page = 40

    # Money, customer details and gateway references are never edited by hand:
    # an order must always match what the customer actually submitted and paid.
    readonly_fields = (
        "order_number",
        "full_name",
        "phone",
        "email",
        "governorate",
        "city",
        "address",
        "notes",
        "payment_method",
        "subtotal",
        "shipping_cost",
        "total_price",
        "currency",
        "fawaterk_invoice_id",
        "fawaterk_invoice_key",
        "fawaterk_payment_method",
        "fawaterk_reference",
        "payment_error",
        "paid_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Order",
            {"fields": ("order_number", "order_status", "created_at", "updated_at")},
        ),
        (
            "Customer",
            {
                "fields": (
                    "full_name",
                    "phone",
                    "email",
                    "governorate",
                    "city",
                    "address",
                    "notes",
                )
            },
        ),
        (
            "Payment",
            {
                "fields": (
                    "payment_method",
                    "payment_status",
                    "subtotal",
                    "shipping_cost",
                    "total_price",
                    "currency",
                    "paid_at",
                    "payment_error",
                )
            },
        ),
        (
            "Gateway references",
            {
                "classes": ("collapse",),
                "fields": (
                    "fawaterk_invoice_id",
                    "fawaterk_invoice_key",
                    "fawaterk_payment_method",
                    "fawaterk_reference",
                ),
            },
        ),
    )
    actions = ("mark_processing", "mark_shipped", "mark_delivered")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("items")

    def has_delete_permission(self, request, obj=None):
        # Paid orders are financial records. Cancel them instead of deleting.
        if obj is not None and obj.is_paid:
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description="Payment", ordering="payment_status")
    def payment_badge(self, obj):
        colours = {
            "paid": "#1e8449",
            "pending": "#b9770e",
            "failed": "#c0392b",
            "cancelled": "#7f8c8d",
            "refunded": "#6c3483",
        }
        colour = colours.get(obj.payment_status, "#7f8c8d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:10px;font-size:11px;">{}</span>',
            colour,
            obj.get_payment_status_display(),
        )

    def _bulk_status(self, request, queryset, status, label):
        updated = queryset.update(order_status=status)
        self.message_user(
            request,
            f"{updated} order(s) marked {label}.",
            messages.SUCCESS,
        )

    @admin.action(description="Mark as processing")
    def mark_processing(self, request, queryset):
        self._bulk_status(request, queryset, Order.OrderStatus.PROCESSING, "processing")

    @admin.action(description="Mark as shipped")
    def mark_shipped(self, request, queryset):
        self._bulk_status(request, queryset, Order.OrderStatus.SHIPPED, "shipped")

    @admin.action(description="Mark as delivered")
    def mark_delivered(self, request, queryset):
        self._bulk_status(request, queryset, Order.OrderStatus.DELIVERED, "delivered")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """Read-only log. Useful when a customer says they paid but the order is
    still pending: find their invoice id here and see exactly what arrived."""

    list_display = (
        "received_at",
        "source",
        "invoice_id",
        "order",
        "signature_valid",
        "processed",
        "message",
    )
    list_filter = ("source", "signature_valid", "processed")
    search_fields = ("invoice_id", "order__order_number", "message")
    readonly_fields = (
        "source",
        "invoice_id",
        "order",
        "payload",
        "signature_valid",
        "processed",
        "message",
        "received_at",
    )
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.site_header = "HANY Administration"
admin.site.site_title = "HANY Admin"
admin.site.index_title = "Store and orders"
