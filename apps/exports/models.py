from django.db import models

from apps.core.models import UUIDModel


class ExportJob(UUIDModel):
    """Background data export (candidates, marks, results) as CSV/XLSX."""

    class ExportType(models.TextChoices):
        CANDIDATES = "CANDIDATES", "Candidates"
        CANDIDATE_LIST = "CANDIDATE_LIST", "Candidate List"
        MARKS = "MARKS", "Marks"
        RESULTS = "RESULTS", "Results"
        ENROLLMENTS = "ENROLLMENTS", "Enrollments"

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="export_jobs")
    export_type = models.CharField(max_length=20, choices=ExportType.choices)
    format = models.CharField(max_length=10, choices=(("CSV", "CSV"), ("XLSX", "Excel")), default="CSV")
    params = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.QUEUED)
    file = models.FileField(upload_to="exports/", null=True, blank=True)
    error = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.export_type} export ({self.status})"
