"""Enforce the order_number uniqueness at the database level.

RECONSTRUCTED FILE -- delivered as 0 bytes in the uploaded archive (an
archive corruption). Inferred with high confidence from context, not
guessed blind:

* store/models.py already declares ``order_number = models.CharField(...,
  unique=True, ...)`` on Order -- this migration is what applies that
  constraint at the database level.
* 0004_backfill_order_data (the previous migration) explicitly backfills a
  unique order_number onto every pre-existing row first -- exactly the
  prerequisite a migration adding a unique constraint needs, and pointless
  otherwise.
* 0006 (the next migration) lists this one as its dependency, confirming the
  name/position but not its content.

IMPORTANT: this is a schema migration touching a live table, not a template
or config file. Please verify this matches your real 0005 migration (e.g.
against Django's migration history table on the production database, or
your own git history) before running it against production data -- if your
real migration also renamed a column or changed a default alongside the
uniqueness constraint, running this reconstruction instead could leave the
migration graph out of sync with the actual database. If in doubt, hold off
on deploying this specific file and send the original.
"""

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
    ]
