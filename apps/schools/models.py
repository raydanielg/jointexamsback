from django.db import models

from apps.core.models import UUIDModel


class School(UUIDModel):
    """A participating examination school. Holds only the identity
    information needed to attribute candidates and results — EMAS is not a
    school-management system."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="centers",
        help_text="Set for examination centers administered by a parent organization.",
    )
    school_name = models.CharField(max_length=255)
    school_code = models.CharField(max_length=30, unique=True)
    registration_number = models.CharField(max_length=60, unique=True, null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to="school_logos/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ("school_name",)
        indexes = [models.Index(fields=("status",)), models.Index(fields=("school_code",))]

    def __str__(self):
        return f"{self.school_name} ({self.school_code})"
