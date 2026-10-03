from django.db import models

from apps.core.models import UUIDModel


class Mark(UUIDModel):
    """One candidate's mark for one exam component. Absent/missing marks
    keep a null value and an explicit status — never auto-zero."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ENTERED = "ENTERED", "Entered"
        ABSENT = "ABSENT", "Absent"
        EXEMPT = "EXEMPT", "Exempt"
        MISSING = "MISSING", "Missing"
        INVALID = "INVALID", "Invalid"

    exam_candidate = models.ForeignKey(
        "enrollment.ExaminationCandidate", on_delete=models.CASCADE, related_name="marks"
    )
    component = models.ForeignKey(
        "examinations.ExamComponent", on_delete=models.CASCADE, related_name="marks"
    )
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="+",
        help_text="Denormalized candidate school for scoping.",
    )
    value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    remarks = models.CharField(max_length=255, blank=True)
    entered_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="entered_marks",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("exam_candidate", "component"),
                name="unique_mark_per_candidate_component",
            ),
            models.CheckConstraint(
                check=models.Q(value__gte=0) | models.Q(value__isnull=True),
                name="mark_value_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=("exam_candidate",)),
            models.Index(fields=("component",)),
            models.Index(fields=("school", "component")),
            models.Index(fields=("exam_candidate", "status")),
        ]

    def __str__(self):
        return f"{self.exam_candidate} / {self.component.code}: {self.value}"


class MarkChangeLog(UUIDModel):
    """Immutable audit trail of every mark change."""

    class Action(models.TextChoices):
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Updated"
        CLEARED = "CLEARED", "Cleared"
        STATUS_CHANGE = "STATUS_CHANGE", "Status Change"
        CORRECTION = "CORRECTION", "Correction"

    mark = models.ForeignKey(Mark, on_delete=models.SET_NULL, null=True, related_name="change_logs")
    exam_candidate = models.ForeignKey(
        "enrollment.ExaminationCandidate", on_delete=models.CASCADE, related_name="+"
    )
    component = models.ForeignKey(
        "examinations.ExamComponent", on_delete=models.CASCADE, related_name="+"
    )
    action = models.CharField(max_length=15, choices=Action.choices)
    old_value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    new_value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    old_status = models.CharField(max_length=15, blank=True)
    new_status = models.CharField(max_length=15, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("exam_candidate", "component")),
            models.Index(fields=("mark",)),
        ]

    def __str__(self):
        return f"{self.exam_candidate} {self.old_value}->{self.new_value} by {self.changed_by}"


class MarkComment(UUIDModel):
    """Examiner comments: per-candidate, per-subject or per-examination."""

    class Scope(models.TextChoices):
        CANDIDATE = "CANDIDATE", "Candidate"
        SUBJECT = "SUBJECT", "Subject"
        EXAMINATION = "EXAMINATION", "Examination"

    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="comments"
    )
    exam_candidate = models.ForeignKey(
        "enrollment.ExaminationCandidate", on_delete=models.CASCADE,
        null=True, blank=True, related_name="comments",
    )
    exam_subject = models.ForeignKey(
        "examinations.ExamSubject", on_delete=models.CASCADE,
        null=True, blank=True, related_name="comments",
    )
    scope = models.CharField(max_length=15, choices=Scope.choices, default=Scope.CANDIDATE)
    body = models.TextField()
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ("-created_at",)
