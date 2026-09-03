from django.db import migrations

CUSTOM_PERMS = [
    "change_autoshipment_documents",
    "change_railshipment_documents",
    "delete_autoshipment_documents",
    "delete_railshipment_documents",
]

TARGET_GROUPS = ["documents", "admin"]


def add_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    perms = Permission.objects.filter(
        content_type__app_label="documents",
        codename__in=CUSTOM_PERMS,
    )
    for group_name in TARGET_GROUPS:
        try:
            group = Group.objects.get(name=group_name)
        except Group.DoesNotExist:
            continue
        group.permissions.add(*perms)


def remove_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    perms = Permission.objects.filter(
        content_type__app_label="documents",
        codename__in=CUSTOM_PERMS,
    )
    for group_name in TARGET_GROUPS:
        try:
            group = Group.objects.get(name=group_name)
        except Group.DoesNotExist:
            continue
        group.permissions.remove(*perms)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("documents", "0003_add_edit_delete_permissions"),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
