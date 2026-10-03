from django.db import models

from apps.core.models import UUIDModel


class CandidateList(UUIDModel):
    """Reusable group of candidates used for examination enrollment.
    A candidate may belong to many lists; a list may feed many exams."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ARCHIVED = "ARCHIVED", "Archived"

    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="candidate_lists"
    )
    name = models.CharField(max_length=255)
    cohort = models.CharField(
        max_length=80, blank=True, help_text="Optional grouping label, e.g. 'Form Four 2026'."
    )
    description = models.TextField(blank=True)
    candidates = models.ManyToManyField(
        "candidates.Candidate", through="CandidateListEntry", related_name="list_memberships"
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("school", "name"), name="unique_candidate_list_name_per_school"
            )
        ]

    @property
    def candidate_count(self):
        return self.entries.count()

    def __str__(self):
        return f"{self.name} ({self.school.school_code})"


class CandidateListEntry(UUIDModel):
    list = models.ForeignKey(CandidateList, on_delete=models.CASCADE, related_name="entries")
    candidate = models.ForeignKey(
        "candidates.Candidate", on_delete=models.CASCADE, related_name="list_entries"
    )
    added_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("list", "candidate"), name="unique_candidate_per_list"
            )
        ]
        indexes = [models.Index(fields=("candidate", "list"))]

    def __str__(self):
        return f"{self.candidate} in {self.list}"
