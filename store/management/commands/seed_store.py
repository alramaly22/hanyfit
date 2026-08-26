"""Seed the store with sample products.

Useful for a fresh database or a staging deploy:

    python manage.py seed_store
    python manage.py seed_store --stock 25
    python manage.py seed_store --reset

Idempotent: running it twice does not create duplicates.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from store.models import Product, ProductVariant, SizeChoices

PRODUCTS = [
    {
        "name": "Signature Training Tee — Black",
        "slug": "signature-training-tee-black",
        "price": Decimal("450.00"),
        "compare_at_price": Decimal("550.00"),
        "description": (
            "Heavyweight 240gsm combed cotton with a boxy, slightly cropped cut "
            "that stays put through pressing and pulling. Ribbed collar that will "
            "not stretch out, and a print that survives the wash."
        ),
        "front_image": "images/store/black-front.png",
        "back_image": "images/store/black-back.png",
        "display_order": 1,
        "is_featured": True,
    },
    {
        "name": "Signature Training Tee — White",
        "slug": "signature-training-tee-white",
        "price": Decimal("450.00"),
        "compare_at_price": None,
        "description": (
            "The same heavyweight body as the black tee, in an off-white that does "
            "not go transparent under gym lighting. Pre-shrunk, so the size you "
            "order is the size you keep."
        ),
        "front_image": "images/store/white-front.png",
        "back_image": "images/store/white-back.png",
        "display_order": 2,
        "is_featured": False,
    },
]

SIZES = [SizeChoices.S, SizeChoices.M, SizeChoices.L, SizeChoices.XL]


class Command(BaseCommand):
    help = "Create sample products with size variants and stock."

    def add_arguments(self, parser):
        parser.add_argument(
            "--stock",
            type=int,
            default=15,
            help="Stock to set for each size (default 15).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing products first. Orders are preserved.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        stock = options["stock"]

        if options["reset"]:
            deleted, _ = Product.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} product rows."))

        for spec in PRODUCTS:
            variants_spec = {size: stock for size in SIZES}
            product, created = Product.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "description": spec["description"],
                    "price": spec["price"],
                    "compare_at_price": spec["compare_at_price"],
                    "front_image": spec["front_image"],
                    "back_image": spec["back_image"],
                    "display_order": spec["display_order"],
                    "is_featured": spec["is_featured"],
                    "is_active": True,
                },
            )

            for size, count in variants_spec.items():
                ProductVariant.objects.update_or_create(
                    product=product,
                    size=size,
                    defaults={"stock": count, "is_active": True},
                )

            verb = "Created" if created else "Updated"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {product.name} with {len(variants_spec)} sizes."
                )
            )

        self.stdout.write("")
        self.stdout.write(
            "Product images are referenced as static paths. Add the files under "
            "accounts/static/images/store/ or edit each product in the admin and "
            "paste a full https:// CDN URL instead."
        )
