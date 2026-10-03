import uuid

from django.db import migrations


def backfill(apps, schema_editor):
    Examination = apps.get_model("examinations", "Examination")
    for exam in Examination.objects.filter(
        status="PUBLISHED", public_token__isnull=True
    ):
        exam.public_token = uuid.uuid4()
        exam.save(update_fields=["public_token"])


class Migration(migrations.Migration):
    dependencies = [("examinations", "0003_examination_public_token")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
