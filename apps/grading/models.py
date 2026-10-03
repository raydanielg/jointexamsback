from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import UUIDModel


class GradingScheme(UUIDModel):
    """Configurable grade bands per school, e.g. A=80-100 ... F=0-39."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ARCHIVED = "ARCHIVED", "Archived"

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="grading_schemes")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("school", "name"), name="unique_scheme_name_per_school"),
            models.UniqueConstraint(
                fields=("school",), condition=Q(is_default=True), name="unique_default_scheme_per_school"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.school.school_code})"


class GradeBand(models.Model):
    scheme = models.ForeignKey(GradingScheme, on_delete=models.CASCADE, related_name="bands")
    grade = models.CharField(max_length=5)
    min_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    max_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    points = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_pass = models.BooleanField(default=True)
    remark = models.CharField(max_length=150, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("-min_percentage",)
        constraints = [
            models.UniqueConstraint(fields=("scheme", "grade"), name="unique_grade_per_scheme"),
            models.CheckConstraint(
                check=Q(min_percentage__gte=0) & Q(max_percentage__lte=100)
                & Q(min_percentage__lt=models.F("max_percentage")),
                name="valid_grade_band_range",
            ),
        ]

    def clean(self):
        if self.min_percentage >= self.max_percentage:
            raise ValidationError("min_percentage must be less than max_percentage.")

    def __str__(self):
        return f"{self.grade}: {self.min_percentage}-{self.max_percentage}"


class DivisionBand(models.Model):
    """Division rules per scheme, e.g. Division I for points 7-17."""

    scheme = models.ForeignKey(GradingScheme, on_delete=models.CASCADE, related_name="division_bands")
    name = models.CharField(max_length=10)
    min_points = models.DecimalField(max_digits=5, decimal_places=1)
    max_points = models.DecimalField(max_digits=5, decimal_places=1)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("min_points",)
        constraints = [
            models.UniqueConstraint(fields=("scheme", "name"), name="unique_division_per_scheme"),
            models.CheckConstraint(
                check=Q(min_points__lte=models.F("max_points")), name="valid_division_range"
            ),
        ]

    def __str__(self):
        return f"Division {self.name}: {self.min_points}-{self.max_points}"
