from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import UUIDModel


class Examination(UUIDModel):
    """One examination sitting. May involve a single school or many
    participating schools (joint examinations)."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        READY = "READY", "Ready"
        ACTIVE = "ACTIVE", "Active"
        MARKS_ENTRY = "MARKS_ENTRY", "Marks Entry"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
        FINALIZED = "FINALIZED", "Finalized"
        PUBLISHED = "PUBLISHED", "Published"
        ARCHIVED = "ARCHIVED", "Archived"

    # Allowed workflow transitions (controlled by ExaminationService).
    TRANSITIONS = {
        Status.DRAFT: (Status.READY,),
        Status.READY: (Status.DRAFT, Status.ACTIVE),
        Status.ACTIVE: (Status.MARKS_ENTRY,),
        Status.MARKS_ENTRY: (Status.UNDER_REVIEW,),
        Status.UNDER_REVIEW: (Status.MARKS_ENTRY, Status.FINALIZED),
        Status.FINALIZED: (Status.PUBLISHED,),
        Status.PUBLISHED: (Status.ARCHIVED,),
        Status.ARCHIVED: (),
    }

    class Term(models.TextChoices):
        TERM_1 = "TERM_1", "Term 1"
        TERM_2 = "TERM_2", "Term 2"
        TERM_3 = "TERM_3", "Term 3"
        ANNUAL = "ANNUAL", "Annual"
        MOCK = "MOCK", "Mock"
        OTHER = "OTHER", "Other"

    # Organizing school: the tenant that owns/administers this examination.
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="organized_examinations"
    )
    participating_schools = models.ManyToManyField(
        "schools.School", related_name="examination_participation", blank=True
    )
    candidate_lists = models.ManyToManyField(
        "candidate_lists.CandidateList", related_name="examinations", blank=True
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=30)
    description = models.TextField(blank=True)
    term = models.CharField(max_length=20, choices=Term.choices, default=Term.OTHER)
    period = models.CharField(max_length=80, blank=True, help_text="Free-text period, e.g. 'March 2026'.")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    instructions = models.TextField(blank=True)
    grading_scheme = models.ForeignKey(
        "grading.GradingScheme", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="examinations",
    )
    ranking_config = models.ForeignKey(
        "ranking.RankingConfiguration", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="examinations",
    )
    report_template = models.ForeignKey(
        "reports.ReportTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="examinations",
    )
    sms_template = models.ForeignKey(
        "sms.SMSTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="examinations",
    )
    division_best_subjects = models.PositiveSmallIntegerField(
        default=0, help_text="Best-N subject count used for division points. 0 = all subjects."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    # Public results link — generated on first publish.
    public_token = models.UUIDField(null=True, blank=True, unique=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="finalized_examinations",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("school", "code"), name="unique_exam_code_per_school")
        ]
        indexes = [
            models.Index(fields=("school", "status")),
            models.Index(fields=("status",)),
        ]

    def can_transition_to(self, new_status):
        return new_status in self.TRANSITIONS.get(self.status, ())

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})

    def __str__(self):
        return f"{self.name} ({self.code})"


class ExamSubject(UUIDModel):
    """A subject configured for one examination with its own max marks,
    pass mark and calculation method."""

    class CalculationMethod(models.TextChoices):
        NORMALIZED = "NORMALIZED", "Normalized to configured max"
        WEIGHTED = "WEIGHTED", "Weighted component percentages"
        RAW_TOTAL = "RAW_TOTAL", "Raw component total"

    class MarksStatus(models.TextChoices):
        OPEN = "OPEN", "Open"
        LOCKED = "LOCKED", "Locked"

    examination = models.ForeignKey(Examination, on_delete=models.CASCADE, related_name="exam_subjects")
    subject = models.ForeignKey("subjects.Subject", on_delete=models.CASCADE, related_name="exam_subjects")
    maximum_marks = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    pass_mark = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    weight = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    calculation_method = models.CharField(
        max_length=15, choices=CalculationMethod.choices, default=CalculationMethod.NORMALIZED
    )
    display_order = models.PositiveIntegerField(default=0)
    marks_status = models.CharField(
        max_length=10, choices=MarksStatus.choices, default=MarksStatus.OPEN
    )
    status = models.CharField(
        max_length=15,
        choices=(("ACTIVE", "Active"), ("INACTIVE", "Inactive")),
        default="ACTIVE",
    )

    class Meta:
        ordering = ("display_order", "subject__name")
        constraints = [
            models.UniqueConstraint(
                fields=("examination", "subject"), name="unique_subject_per_exam"
            ),
            models.CheckConstraint(
                check=models.Q(maximum_marks__gt=0), name="exam_subject_max_positive"
            ),
        ]
        indexes = [models.Index(fields=("examination", "status"))]

    def __str__(self):
        return f"{self.subject.name} @ {self.examination.code}"


class ExamComponent(UUIDModel):
    """A sub-assessment within an ExamSubject (Paper 1, Practical...).
    Subjects with a single component behave as one-mark exams."""

    exam_subject = models.ForeignKey(
        ExamSubject, on_delete=models.CASCADE, related_name="components"
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    maximum_marks = models.DecimalField(max_digits=8, decimal_places=2)
    weight = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Percentage weight used for WEIGHTED calculation; weights must sum to 100.",
    )
    required = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=15,
        choices=(("ACTIVE", "Active"), ("INACTIVE", "Inactive")),
        default="ACTIVE",
    )

    class Meta:
        ordering = ("display_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("exam_subject", "code"), name="unique_component_code_per_subject"
            ),
            models.CheckConstraint(
                check=models.Q(maximum_marks__gt=0), name="component_max_positive"
            ),
        ]

    def __str__(self):
        return f"{self.exam_subject.subject.code}/{self.code}"
