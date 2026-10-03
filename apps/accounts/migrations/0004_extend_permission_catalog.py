from django.db import migrations

NEW_PERMISSIONS = [
    ("candidates.view", "View candidates"),
    ("candidates.create", "Create candidates"),
    ("candidates.update", "Update candidates"),
    ("candidates.delete", "Delete candidates"),
    ("candidates.import", "Import candidates"),
    ("candidates.export", "Export candidates"),
    ("subjects.view", "View subjects"),
    ("subjects.manage", "Manage subjects"),
    ("users.manage", "Manage users (status, roles)"),
    ("settings.manage", "Manage system settings"),
    ("exams.publish", "Publish examination results"),
    ("exams.archive", "Archive examinations"),
    ("marks.import", "Import marks"),
    ("marks.export", "Export marks"),
    ("marks.finalize", "Finalize marks"),
    ("marks.approve", "Approve marks"),
    ("results.calculate", "Calculate results"),
    ("results.review", "Review results"),
    ("results.approve", "Approve results"),
    ("results.correct", "Approve result corrections"),
    ("results.export", "Export results"),
    ("reports.export", "Export reports"),
    ("reports.print", "Print reports"),
    ("reports.manage_templates", "Manage report templates"),
]

ROLE_PERMISSIONS = {
    "EXAM_ADMIN": [code for code, _ in NEW_PERMISSIONS],
    "SCHOOL_COORDINATOR": [
        "candidates.view", "candidates.create", "candidates.import",
        "subjects.view", "marks.view", "results.view",
        "reports.view", "reports.export",
    ],
    "MARKS_ENTRY": [
        "candidates.view", "marks.import", "marks.export",
    ],
    "REPORT_VIEWER": [
        "candidates.view", "results.export", "reports.export", "reports.print",
    ],
}


def seed(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")
    for code, name in NEW_PERMISSIONS:
        Permission.objects.get_or_create(code=code, defaults={"name": name})
    all_perms = {p.code: p for p in Permission.objects.all()}
    for role, codes in ROLE_PERMISSIONS.items():
        for code in codes:
            if code in all_perms:
                RolePermission.objects.get_or_create(
                    role=role, permission=all_perms[code]
                )


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_seed_permission_catalog")]
    operations = [migrations.RunPython(seed, unseed)]
