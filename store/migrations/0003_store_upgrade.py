"""Schema changes for the rebuilt store.

Split across three migrations on purpose:

* 0003 adds the columns, with order_number and access_token deliberately
  *not* unique yet. A callable default is evaluated once by the database
  backend, so adding them as unique in one step would try to write the same
  value into every existing row and fail the constraint.
* 0004 fills in per-row values for the existing orders.
* 0005 applies the unique constraint and the real defaults.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0002_order_order_status"),
    ]

    operations = [
        # ------------------------------------------------------------------
        # Product
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="product",
            name="compare_at_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Original price, shown struck through. Leave empty if not on sale.",
                max_digits=10,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="is_featured",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="product",
            name="display_order",
            field=models.PositiveIntegerField(
                default=0, help_text="Lower numbers appear first."
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="product",
            name="slug",
            field=models.SlugField(max_length=220, unique=True),
        ),
        migrations.AlterField(
            model_name="product",
            name="price",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="front_image",
            field=models.CharField(
                help_text="Static path (images/store/tee-front.png) or full https:// URL.",
                max_length=500,
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="back_image",
            field=models.CharField(
                blank=True,
                help_text="Optional second image, revealed on hover.",
                max_length=500,
            ),
        ),
        migrations.AlterModelOptions(
            name="product",
            options={"ordering": ("display_order", "-created_at")},
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(
                fields=["is_active", "display_order"],
                name="store_produ_is_acti_de0628_idx",
            ),
        ),
        # ------------------------------------------------------------------
        # ProductVariant
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ProductVariant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "size",
                    models.CharField(
                        choices=[
                            ("XS", "XS"),
                            ("S", "S"),
                            ("M", "M"),
                            ("L", "L"),
                            ("XL", "XL"),
                            ("XXL", "2XL"),
                        ],
                        max_length=5,
                    ),
                ),
                ("stock", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="variants",
                        to="store.product",
                    ),
                ),
            ],
            options={"ordering": ("product", "size")},
        ),
        migrations.AddConstraint(
            model_name="productvariant",
            constraint=models.UniqueConstraint(
                fields=("product", "size"), name="unique_product_size"
            ),
        ),
        # ------------------------------------------------------------------
        # Order
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name="order",
            name="order_number",
            field=models.CharField(default="", editable=False, max_length=32),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="access_token",
            field=models.CharField(
                db_index=True, default="", editable=False, max_length=64
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="subtotal",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_cost",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="order",
            name="currency",
            field=models.CharField(default="EGP", max_length=8),
        ),
        migrations.AddField(
            model_name="order",
            name="fawaterk_invoice_id",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="order",
            name="fawaterk_invoice_key",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="order",
            name="fawaterk_payment_method",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="order",
            name="fawaterk_reference",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="order",
            name="payment_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="order",
            name="paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="tiktok_event_id",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="order",
            name="tiktok_purchase_sent",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="order",
            name="session_key",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="order",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="order",
            name="total_price",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AlterField(
            model_name="order",
            name="payment_method",
            field=models.CharField(
                choices=[("cash", "Cash on delivery"), ("online", "Online payment")],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="order",
            name="payment_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("paid", "Paid"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                    ("refunded", "Refunded"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="order",
            name="order_status",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("confirmed", "Confirmed"),
                    ("processing", "Processing"),
                    ("shipped", "Shipped"),
                    ("delivered", "Delivered"),
                    ("cancelled", "Cancelled"),
                ],
                default="new",
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name="order",
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=["payment_status", "-created_at"],
                name="store_order_payment_1423bc_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=["order_status", "-created_at"],
                name="store_order_order_s_97474c_idx",
            ),
        ),
        # ------------------------------------------------------------------
        # OrderItem
        # ------------------------------------------------------------------
        migrations.RenameField(
            model_name="orderitem",
            old_name="price",
            new_name="unit_price",
        ),
        migrations.AddField(
            model_name="orderitem",
            name="product_name",
            field=models.CharField(default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="orderitem",
            name="product_image",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="variant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="store.productvariant",
            ),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="store.product",
            ),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="size",
            field=models.CharField(
                choices=[
                    ("XS", "XS"),
                    ("S", "S"),
                    ("M", "M"),
                    ("L", "L"),
                    ("XL", "XL"),
                    ("XXL", "2XL"),
                ],
                max_length=5,
            ),
        ),
        migrations.AlterModelOptions(
            name="orderitem",
            options={"ordering": ("id",)},
        ),
        # ------------------------------------------------------------------
        # WebhookEvent
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="WebhookEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("fawaterk_paid", "Fawaterk - paid"),
                            ("fawaterk_failed", "Fawaterk - failed"),
                            ("fawaterk_expired", "Fawaterk - expired"),
                            ("fawaterk_refund", "Fawaterk - refund"),
                            ("unknown", "Unknown"),
                        ],
                        default="unknown",
                        max_length=32,
                    ),
                ),
                ("invoice_id", models.CharField(blank=True, db_index=True, max_length=64)),
                ("payload", models.JSONField(default=dict)),
                ("signature_valid", models.BooleanField(default=False)),
                ("processed", models.BooleanField(default=False)),
                ("message", models.CharField(blank=True, max_length=255)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="webhook_events",
                        to="store.order",
                    ),
                ),
            ],
            options={
                "verbose_name": "Webhook event",
                "ordering": ("-received_at",),
            },
        ),
    ]
