from django.db import models

from apps.core.models import UUIDModel


class SchoolSetting(UUIDModel):
    """Per-school configuration, e.g. candidate_number_pattern,
    notification preferences, report defaults."""

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="settings")
    key = models.CharField(max_length=80)
    value = models.JSONField()
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("school", "key"), name="unique_setting_key_per_school")
        ]

    def __str__(self):
        return f"{self.school.school_code}.{self.key}"


class SystemSetting(UUIDModel):
    """Global system configuration (super admin only)."""

    key = models.CharField(max_length=80, unique=True)
    value = models.JSONField()
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.key
