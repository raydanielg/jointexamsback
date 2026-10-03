from django.db import migrations

# Centers = examination centers (child schools) managed by an organization.
NEW_PERMISSIONS = [
    ("centers.view", "View examination centers"),
    ("centers.manage", "Create and manage examination centers"),
]
GRANT_TO = ("EXAM_ADMIN",)


def forwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")
    for code, name in NEW_PERMISSIONS:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"name": name})
        for role in GRANT_TO:
            RolePermission.objects.get_or_create(role=role, permission=perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Permission.objects.filter(code__in=[c for c, _ in NEW_PERMISSIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_exam_admin_no_admin_perms"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
