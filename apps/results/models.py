from django.db import models

from apps.core.models import UUIDModel


class ResultStatus(models.TextChoices):
    SCORED = "SCORED", "Scored"
    ABSENT = "ABSENT", "Absent"
    EXEMPT = "EXEMPT", "Exempt"
    INCOMPLETE = "INCOMPLETE", "Incomplete"


class CandidateSubjectResult(UUIDModel):
    """One candidate's calculated result in one exam subject."""

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="+")
    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="subject_results"
    )
    exam_candidate = models.ForeignKey(
        "enrollment.ExaminationCandidate", on_delete=models.CASCADE, related_name="subject_results"
    )
    exam_subject = models.ForeignKey(
        "examinations.ExamSubject", on_delete=models.CASCADE, related_name="results"
    )
    score = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_score = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    percentage = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=5, blank=True)
    points = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    is_pass = models.BooleanField(default=False)
    remark = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=15, choices=ResultStatus.choices, default=ResultStatus.INCOMPLETE)
    position = models.PositiveIntegerField(null=True, blank=True)
    school_position = models.PositiveIntegerField(null=True, blank=True)
    component_breakdown = models.JSONField(default=dict, blank=True)
    calc_version = models.PositiveIntegerField(default=1)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("exam_candidate", "exam_subject"),
                name="unique_subject_result_per_candidate",
            )
        ]
        indexes = [
            models.Index(fields=("exam_subject", "percentage")),
            models.Index(fields=("examination", "position")),
            models.Index(fields=("school", "exam_subject")),
        ]

    def __str__(self):
        return f"{self.exam_candidate} / {self.exam_subject.subject.name}: {self.percentage}"


class CandidateExamResult(UUIDModel):
    """One candidate's overall result in one examination."""

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="+")
    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="exam_results"
    )
    exam_candidate = models.OneToOneField(
        "enrollment.ExaminationCandidate", on_delete=models.CASCADE, related_name="exam_result"
    )
    total_score = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    average_percentage = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=5, blank=True)
    points_sum = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    division = models.CharField(max_length=10, blank=True)
    position = models.PositiveIntegerField(null=True, blank=True)
    school_position = models.PositiveIntegerField(null=True, blank=True)
    list_position = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=ResultStatus.choices, default=ResultStatus.INCOMPLETE)
    subject_count = models.PositiveIntegerField(default=0)
    scored_subjects = models.PositiveIntegerField(default=0)
    calc_version = models.PositiveIntegerField(default=1)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position",)
        indexes = [
            models.Index(fields=("examination", "position")),
            models.Index(fields=("school", "examination")),
        ]

    def __str__(self):
        return f"{self.exam_candidate} @ {self.examination.code}"


class ResultSnapshot(UUIDModel):
    """Frozen calculation output at finalize/publish/correction time."""

    class Kind(models.TextChoices):
        FINALIZED = "FINALIZED", "Finalized"
        PUBLISHED = "PUBLISHED", "Published"
        CORRECTED = "CORRECTED", "Corrected"

    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="snapshots"
    )
    kind = models.CharField(max_length=15, choices=Kind.choices)
    version = models.PositiveIntegerField()
    grading_scheme_snapshot = models.JSONField(default=dict, blank=True)
    ranking_config_snapshot = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(
                fields=("examination", "kind", "version"), name="unique_snapshot_version"
            )
        ]

    def __str__(self):
        return f"{self.examination.code} {self.kind} v{self.version}"


class PredefinedComment(UUIDModel):
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="predefined_comments"
    )
    code = models.CharField(max_length=20)
    text = models.CharField(max_length=255)
    category = models.CharField(max_length=50, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("school", "code"), name="unique_comment_code_per_school"
            )
        ]

    def __str__(self):
        return self.code


class ResultCorrectionRequest(UUIDModel):
    """Audited workflow for fixing published results."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        APPLIED = "APPLIED", "Applied"

    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="corrections"
    )
    exam_candidate = models.ForeignKey(
        "enrollment.ExaminationCandidate", on_delete=models.CASCADE, related_name="corrections"
    )
    component = models.ForeignKey(
        "examinations.ExamComponent", on_delete=models.CASCADE, related_name="corrections"
    )
    old_value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    old_status = models.CharField(max_length=15, blank=True)
    requested_value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    requested_status = models.CharField(max_length=15, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("examination", "status"))]
