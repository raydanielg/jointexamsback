from django.db import models

from apps.core.models import UUIDModel


class RankingConfiguration(UUIDModel):
    """Configurable ranking rules attached to examinations."""

    class Method(models.TextChoices):
        COMPETITION = "COMPETITION", "Competition (1, 2, 2, 4)"
        DENSE = "DENSE", "Dense (1, 2, 2, 3)"
        ORDINAL = "ORDINAL", "Ordinal (1, 2, 3, 4)"

    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="ranking_configs",
        null=True, blank=True,
    )
    name = models.CharField(max_length=100)
    method = models.CharField(max_length=15, choices=Method.choices, default=Method.COMPETITION)
    # Which per-candidate metric drives overall ranking.
    rank_by = models.CharField(
        max_length=20,
        choices=(
            ("average_percentage", "Average percentage"),
            ("total_score", "Total score"),
        ),
        default="average_percentage",
    )
    include_absent = models.BooleanField(
        default=False, help_text="Rank absent/incomplete candidates at the bottom."
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("school", "name"),
                name="unique_ranking_config_name_per_school",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.method})"
