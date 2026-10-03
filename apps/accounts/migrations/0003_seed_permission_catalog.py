from django.db import migrations

PERMISSIONS = [
    ("users.view", "View users"),
    ("users.create", "Create and invite users"),
    ("users.update", "Update users"),
    ("users.disable", "Suspend or deactivate users"),
    ("exams.view", "View examinations"),
    ("exams.create", "Create examinations"),
    ("exams.update", "Configure examinations"),
    ("exams.transition", "Change examination status"),
    ("marks.view", "View marks"),
    ("marks.enter", "Enter marks"),
    ("marks.update", "Update marks"),
    ("results.view", "View results"),
    ("results.finalize", "Finalize results"),
    ("results.publish", "Publish results"),
    ("results.correct", "Approve result corrections"),
    ("reports.view", "View reports"),
    ("reports.generate", "Generate reports"),
    ("sms.send", "Send result SMS"),
    ("audit.view", "View audit logs"),
]

ROLE_PERMISSIONS = {
    "EXAM_ADMIN": [code for code, _ in PERMISSIONS],
    "SCHOOL_COORDINATOR": [
        "users.view", "exams.view", "marks.view", "results.view",
        "reports.view", "reports.generate",
    ],
    "MARKS_ENTRY": ["exams.view", "marks.view", "marks.enter", "marks.update"],
    "REPORT_VIEWER": [
        "exams.view", "results.view", "reports.view", "reports.generate",
    ],
}


def seed(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")
    perms = {}
    for code, name in PERMISSIONS:
        perms[code], _ = Permission.objects.get_or_create(
            code=code, defaults={"name": name}
        )
    for role, codes in ROLE_PERMISSIONS.items():
        for code in codes:
            RolePermission.objects.get_or_create(role=role, permission=perms[code])


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_permission_user_status_emailverificationtoken_and_more"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
