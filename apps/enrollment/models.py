from django.db import models

from apps.core.models import UUIDModel


class ExaminationCandidate(UUIDModel):
    """A candidate enrolled in one examination. Created via candidate
    lists (bulk) or explicit selection; never duplicated per exam."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        ABSENT = "ABSENT", "Absent"
        DISQUALIFIED = "DISQUALIFIED", "Disqualified"
        COMPLETED = "COMPLETED", "Completed"

    #: Statuses that count as currently participating in the examination.
    PARTICIPATING = (Status.ACTIVE, Status.COMPLETED)

    examination = models.ForeignKey(
        "examinations.Examination", on_delete=models.CASCADE, related_name="candidates"
    )
    candidate = models.ForeignKey(
        "candidates.Candidate", on_delete=models.CASCADE, related_name="exam_enrollments"
    )
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="exam_candidates",
        help_text="Denormalized candidate school for school-level scoping and results.",
    )
    candidate_number = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    source_list = models.ForeignKey(
        "candidate_lists.CandidateList", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="exam_candidates",
    )
    enrolled_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ("candidate_number",)
        constraints = [
            models.UniqueConstraint(
                fields=("examination", "candidate"), name="unique_candidate_per_exam"
            ),
            models.UniqueConstraint(
                fields=("examination", "candidate_number"),
                condition=models.Q(candidate_number__gt=""),
                name="unique_candidate_number_per_exam",
            ),
        ]
        indexes = [
            models.Index(fields=("examination", "status")),
            models.Index(fields=("examination", "school")),
            models.Index(fields=("candidate", "examination")),
        ]

    def __str__(self):
        return f"{self.candidate} @ {self.examination.code}"
