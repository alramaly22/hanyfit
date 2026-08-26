"""Backfill the data the new columns need.

Runs against whatever is already in the live database:

* every existing order gets its own order number and access token;
* order_status "pending" (the old default) becomes "new";
* order subtotals are derived from their line items, since the old schema only
  stored a single total;
* order items get a snapshot of their product's name and image, so the order
  survives the product being renamed or deleted later;
* the two seeded products get a full set of size variants, because the old
  schema had no stock records at all and every product would otherwise show as
  sold out.
"""

import secrets
from decimal import Decimal

from django.db import migrations
from django.utils import timezone

DEFAULT_SIZES = ["S", "M", "L", "XL"]
DEFAULT_STOCK = 10


def forwards(apps, schema_editor):
    Order = apps.get_model("store", "Order")
    OrderItem = apps.get_model("store", "OrderItem")
    Product = apps.get_model("store", "Product")
    ProductVariant = apps.get_model("store", "ProductVariant")

    # --- Orders ---------------------------------------------------------
    used_numbers = set()
    for order in Order.objects.all().iterator():
        created = order.created_at or timezone.now()
        stamp = created.strftime("%y%m%d")

        number = f"HA-{stamp}-{secrets.token_hex(3).upper()}"
        while number in used_numbers or Order.objects.filter(order_number=number).exists():
            number = f"HA-{stamp}-{secrets.token_hex(3).upper()}"
        used_numbers.add(number)

        order.order_number = number
        order.access_token = secrets.token_urlsafe()

        if order.order_status == "pending":
            order.order_status = "new"

        # The old schema had no subtotal or shipping columns. Treat the stored
        # total as the subtotal, which is what it was.
        if not order.subtotal:
            items_total = sum(
                (item.unit_price or Decimal("0")) * item.quantity
                for item in order.items.all()
            )
            order.subtotal = items_total or order.total_price or Decimal("0")
        if not order.total_price:
            order.total_price = order.subtotal
        if not order.currency:
            order.currency = "EGP"

        order.save()

    # --- Order items ----------------------------------------------------
    for item in OrderItem.objects.select_related("product").iterator():
        changed = False
        if not item.product_name:
            item.product_name = item.product.name if item.product else "Unknown item"
            changed = True
        if not item.product_image and item.product:
            item.product_image = item.product.front_image
            changed = True
        if changed:
            item.save(update_fields=["product_name", "product_image"])

    # --- Product variants ------------------------------------------------
    for product in Product.objects.all().iterator():
        if product.variants.exists():
            continue
        ProductVariant.objects.bulk_create(
            [
                ProductVariant(
                    product=product,
                    size=size,
                    stock=DEFAULT_STOCK,
                    is_active=True,
                )
                for size in DEFAULT_SIZES
            ]
        )


def backwards(apps, schema_editor):
    """Nothing to undo: the columns themselves are removed by 0003's reverse."""


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0003_store_upgrade"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
