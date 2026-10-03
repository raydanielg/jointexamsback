from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import UUIDModel


class Candidate(UUIDModel):
    """An examination candidate. Examination-focused identity only —
    guardian contact exists solely for result notifications."""

    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"
        UNSPECIFIED = "UNSPECIFIED", "Unspecified"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="candidates")
    candidate_number = models.CharField(
        max_length=40,
        help_text="Candidate/admission number, unique within the school.",
    )
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    gender = models.CharField(max_length=12, choices=Gender.choices)
    phone = models.CharField(max_length=20, blank=True)
    guardian_name = models.CharField(max_length=150, blank=True)
    guardian_phone = models.CharField(max_length=20, blank=True)
    guardian_phone_2 = models.CharField(max_length=20, blank=True)
    photo = models.ImageField(upload_to="candidate_photos/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ("last_name", "first_name")
        constraints = [
            models.UniqueConstraint(
                fields=("school", "candidate_number"), name="unique_candidate_number_per_school"
            )
        ]
        indexes = [
            models.Index(fields=("school", "candidate_number")),
            models.Index(fields=("school", "status")),
            models.Index(fields=("last_name", "first_name")),
        ]

    @property
    def full_name(self):
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)

    def clean(self):
        for field in ("phone", "guardian_phone", "guardian_phone_2"):
            value = getattr(self, field)
            if value:
                from .validators import validate_phone_number

                try:
                    validate_phone_number(value)
                except ValidationError as exc:
                    raise ValidationError({field: exc.messages})

    def __str__(self):
        return f"{self.full_name} ({self.candidate_number})"
