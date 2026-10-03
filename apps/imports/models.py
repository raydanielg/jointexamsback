from django.db import models

from apps.core.models import UUIDModel


class ImportSession(UUIDModel):
    class ImportType(models.TextChoices):
        CANDIDATES = "CANDIDATES", "Candidates"
        MARKS = "MARKS", "Marks"

    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "Uploaded"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="import_sessions")
    import_type = models.CharField(max_length=20, choices=ImportType.choices)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.UPLOADED)
    file = models.FileField(upload_to="imports/")
    error_report = models.FileField(upload_to="import_errors/", null=True, blank=True)
    params = models.JSONField(default=dict, blank=True)
    strict = models.BooleanField(
        default=True,
        help_text="When true any row error aborts the whole import; when false valid rows commit.",
    )
    total_rows = models.PositiveIntegerField(default=0)
    success_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    updated_rows = models.PositiveIntegerField(default=0)
    skipped_rows = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.import_type} import {self.pk} ({self.status})"
