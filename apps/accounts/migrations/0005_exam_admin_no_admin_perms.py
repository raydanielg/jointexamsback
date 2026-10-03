from django.db import migrations

# Administration permissions stay reserved for super admins — organisation
# admins (EXAM_ADMIN) manage examinations but not system administration.
ADMIN_ONLY = (
    "users.view",
    "users.create",
    "users.update",
    "users.disable",
    "users.manage",
    "audit.view",
    "settings.manage",
)


def forwards(apps, schema_editor):
    RolePermission = apps.get_model("accounts", "RolePermission")
    Permission = apps.get_model("accounts", "Permission")
    perms = Permission.objects.filter(code__in=ADMIN_ONLY)
    RolePermission.objects.filter(role="EXAM_ADMIN", permission__in=perms).delete()


def backwards(apps, schema_editor):
    RolePermission = apps.get_model("accounts", "RolePermission")
    Permission = apps.get_model("accounts", "Permission")
    perms = Permission.objects.filter(code__in=ADMIN_ONLY)
    RolePermission.objects.bulk_create(
        [RolePermission(role="EXAM_ADMIN", permission=p) for p in perms],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_extend_permission_catalog"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
