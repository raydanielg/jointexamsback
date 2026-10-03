from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.core.models import UUIDModel


class Subject(UUIDModel):
    """A subject. ``school`` may be null for globally shared subjects;
    school-specific subjects are isolated per school."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, null=True, blank=True,
        related_name="subjects",
        help_text="Null means the subject is global/shared across schools.",
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30)
    short_name = models.CharField(max_length=30, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("school", "code"), name="unique_subject_code_per_school"),
            models.UniqueConstraint(
                Lower("code"), condition=Q(school__isnull=True), name="unique_global_subject_code"
            ),
        ]
        indexes = [models.Index(fields=("school", "status"))]

    def __str__(self):
        scope = self.school.school_code if self.school else "GLOBAL"
        return f"{self.name} [{scope}]"
