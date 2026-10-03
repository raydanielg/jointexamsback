from django.db import models

from apps.core.models import UUIDModel


class ReportType(models.TextChoices):
    CANDIDATE_RESULT = "CANDIDATE_RESULT", "Candidate Result"
    RESULT_SLIP = "RESULT_SLIP", "Result Slip"
    FULL_RESULTS = "FULL_RESULTS", "Full Examination Results"
    SUBJECT_RESULTS = "SUBJECT_RESULTS", "Subject Results"
    MARK_SHEET = "MARK_SHEET", "Mark Sheet"
    SCHOOL_RESULTS = "SCHOOL_RESULTS", "School Results"
    POSITION_REPORT = "POSITION_REPORT", "Candidate Position Report"
    SUBJECT_POSITIONS = "SUBJECT_POSITIONS", "Subject Position Report"
    SCHOOL_SUMMARY = "SCHOOL_SUMMARY", "School Summary"
    EXAM_SUMMARY = "EXAM_SUMMARY", "Examination Summary"
    GRADE_DISTRIBUTION = "GRADE_DISTRIBUTION", "Grade Distribution"
    PERFORMANCE_ANALYSIS = "PERFORMANCE_ANALYSIS", "Performance Analysis"
    MISSING_MARKS = "MISSING_MARKS", "Missing Marks Report"
    ABSENT_CANDIDATES = "ABSENT_CANDIDATES", "Absent Candidates Report"
    MARKS_COMPLETION = "MARKS_COMPLETION", "Marks Completion Report"
    AUDIT = "AUDIT", "Audit Report"


class ReportTemplate(UUIDModel):
    """Configurable report layout — attached to schools/examinations."""

    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="report_templates"
    )
    name = models.CharField(max_length=120)
    report_type = models.CharField(max_length=30, choices=ReportType.choices)
    header = models.TextField(blank=True)
    footer = models.TextField(blank=True)
    show_logo = models.BooleanField(default=True)
    show_positions = models.BooleanField(default=True)
    show_division = models.BooleanField(default=True)
    show_comments = models.BooleanField(default=True)
    signature_labels = models.JSONField(
        default=list, blank=True, help_text='e.g. ["Class Teacher", "Head of School"]'
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("school", "name"), name="unique_report_template_name_per_school"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.report_type})"


class ReportJob(UUIDModel):
    """Asynchronous report generation job."""

    class Format(models.TextChoices):
        PDF = "PDF", "PDF"
        XLSX = "XLSX", "Excel"
        CSV = "CSV", "CSV"
        HTML = "HTML", "HTML Preview"

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="report_jobs")
    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="report_jobs"
    )
    report_type = models.CharField(max_length=30, choices=ReportType.choices)
    format = models.CharField(max_length=10, choices=Format.choices, default=Format.PDF)
    params = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.QUEUED)
    file = models.FileField(upload_to="reports/", null=True, blank=True)
    error = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("school", "status"))]

    def __str__(self):
        return f"{self.report_type} for {self.examination.code} ({self.status})"
