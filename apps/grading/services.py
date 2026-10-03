from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import GradingScheme


class GradeCalculationService:
    """Centralized grade lookups. All percentage->grade resolution goes
    through this service so grading logic is never duplicated."""

    @staticmethod
    def default_scheme(school):
        scheme = GradingScheme.objects.filter(
            school=school, is_default=True, status=GradingScheme.Status.ACTIVE
        ).first()
        if scheme is None:
            scheme = GradingScheme.objects.filter(
                school=school, status=GradingScheme.Status.ACTIVE
            ).first()
        if scheme is None and school is not None:
            scheme = GradeCalculationService.ensure_default_scheme(school)
        return scheme

    @staticmethod
    def grade_for(percentage, scheme):
        """Return (grade, points, is_pass, remark) or (None, None, None, '')."""
        if percentage is None or scheme is None:
            return None, None, None, ""
        percentage = Decimal(str(percentage))
        band = (
            scheme.bands.filter(min_percentage__lte=percentage, max_percentage__gte=percentage)
            .order_by("-min_percentage")
            .first()
        )
        if band is None:
            # Boundary rounding: fall back to the closest band.
            band = scheme.bands.order_by("-min_percentage").first()
            if band is None:
                return None, None, None, ""
        return band.grade, band.points, band.is_pass, band.remark

    @staticmethod
    def division_for(points_sum, scheme):
        if points_sum is None or scheme is None:
            return None
        band = scheme.division_bands.filter(
            min_points__lte=points_sum, max_points__gte=points_sum
        ).first()
        return band.name if band else None

    @staticmethod
    def ensure_default_scheme(school):
        """Create the standard Tanzanian grading scheme for an organization
        if it has none. A=75-100, B=65-75, C=45-65, D=30-45, F=0-30 with
        CSEE division bands. Returns the scheme."""
        from decimal import Decimal as D

        from .models import GradeBand, DivisionBand

        scheme = GradingScheme.objects.filter(
            school=school, status=GradingScheme.Status.ACTIVE
        ).first()
        if scheme:
            return scheme
        scheme = GradingScheme.objects.create(
            school=school,
            name="Standard Grading",
            description="A: 75-100, B: 65-75, C: 45-65, D: 30-45, F: below 30.",
            is_default=True,
        )
        for grade, lo, hi, pts in (
            ("A", 75, 100, 1), ("B", 65, 75, 2), ("C", 45, 65, 3),
            ("D", 30, 45, 4), ("F", 0, 30, 5),
        ):
            GradeBand.objects.create(
                scheme=scheme, grade=grade,
                min_percentage=D(lo), max_percentage=D(hi),
                points=pts, is_pass=grade != "F",
            )
        for name, lo, hi in (
            ("I", 7, 17), ("II", 18, 21), ("III", 22, 25),
            ("IV", 26, 33), ("0", 34, 35),
        ):
            DivisionBand.objects.create(
                scheme=scheme, name=name, min_points=D(lo), max_points=D(hi)
            )
        return scheme

    @staticmethod
    def validate_scheme(scheme):
        """Ensure grade bands fully cover 0-100 without overlaps."""
        bands = list(scheme.bands.order_by("min_percentage"))
        if not bands:
            raise ValidationError("Grading scheme has no grade bands.")
        cursor = Decimal("0")
        errors = []
        for band in bands:
            if band.min_percentage > cursor:
                errors.append(f"Gap before grade {band.grade} ({cursor}-{band.min_percentage}).")
            if band.min_percentage < cursor:
                errors.append(f"Overlap at grade {band.grade}.")
            cursor = band.max_percentage
        if cursor < Decimal("100"):
            errors.append(f"Scheme does not cover {cursor}-100.")
        if errors:
            raise ValidationError({"bands": errors})
        return True
