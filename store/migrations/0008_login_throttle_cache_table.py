"""Create the database-backed cache table (see settings.CACHES).

Used by the dashboard login throttle in store/dashboard.py. A DB-backed
cache is used instead of local memory specifically because Vercel's
serverless functions do not reliably share process memory between requests.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", "django_cache")


def drop_cache_table(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS django_cache")


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0007_dashboard_access_group"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
