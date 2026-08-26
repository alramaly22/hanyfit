"""Apply the unique constraint and real defaults now that every row is filled."""

import secrets

from django.db import migrations, models

import store.models


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0004_backfill_order_data"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="order_number",
            field=models.CharField(
                default=store.models.generate_order_number,
                editable=False,
                max_length=32,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="order",
            name="access_token",
            field=models.CharField(
                db_index=True,
                default=secrets.token_urlsafe,
                editable=False,
                max_length=64,
            ),
        ),
    ]
