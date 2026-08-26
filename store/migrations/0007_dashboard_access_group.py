"""Create the 'Store Dashboard' group and attach the access_dashboard permission.

The owner grants the client access to /dashboard/ by creating that person a
normal Django user (is_staff=False is fine, is_superuser=False) and adding
them to this group from /admin/ -- Users > (user) > Groups. They never need
is_staff or is_superuser, and therefore never see the admin's own UI, module
list or "add another user" controls.
"""

from django.db import migrations


def create_dashboard_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Order = apps.get_model("store", "Order")

    content_type = ContentType.objects.get_for_model(Order)
    permission, _ = Permission.objects.get_or_create(
        codename="access_dashboard",
        content_type=content_type,
        defaults={"name": "Can access the store dashboard"},
    )

    group, _ = Group.objects.get_or_create(name="Store Dashboard")
    group.permissions.add(permission)


def remove_dashboard_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="Store Dashboard").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0006_alter_order_options_order_country_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(create_dashboard_group, remove_dashboard_group),
    ]
