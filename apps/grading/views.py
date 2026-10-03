from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import ok

from .models import DivisionBand, GradeBand, GradingScheme
from .serializers import DivisionBandSerializer, GradeBandSerializer, GradingSchemeSerializer
from .services import GradeCalculationService


class GradingSchemeViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = GradingSchemeSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = GradingScheme.objects.prefetch_related("bands", "division_bands")
    filterset_fields = ("is_default", "status")
    search_fields = ("name",)
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    write_roles = ("EXAM_ADMIN", "EXAM_ADMIN")

    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        scheme = self.get_object()
        GradingScheme.objects.filter(school=scheme.school, is_default=True).update(is_default=False)
        scheme.is_default = True
        scheme.save(update_fields=["is_default", "updated_at"])
        return ok(message="Default grading scheme updated.")

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        try:
            GradeCalculationService.validate_scheme(self.get_object())
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return ok(message="Grading scheme is valid.")

    @action(detail=True, methods=["post"], url_path="add-band")
    def add_band(self, request, pk=None):
        scheme = self.get_object()
        serializer = GradeBandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(scheme=scheme)
        return ok(serializer.data, message="Grade band added.")

    @action(detail=True, methods=["post"], url_path="add-division-band")
    def add_division_band(self, request, pk=None):
        scheme = self.get_object()
        serializer = DivisionBandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(scheme=scheme)
        return ok(serializer.data, message="Division band added.")

    @action(detail=False, methods=["post"], url_path="create-with-bands")
    @transaction.atomic
    def create_with_bands(self, request):
        """Create a scheme together with grade and division bands in one call."""
        serializer = GradingSchemeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        scheme = serializer.save()
        for band in request.data.get("bands", []):
            band_serializer = GradeBandSerializer(data=band)
            band_serializer.is_valid(raise_exception=True)
            band_serializer.save(scheme=scheme)
        for band in request.data.get("division_bands", []):
            div_serializer = DivisionBandSerializer(data=band)
            div_serializer.is_valid(raise_exception=True)
            div_serializer.save(scheme=scheme)
        if scheme.is_default:
            GradingScheme.objects.filter(school=scheme.school, is_default=True).exclude(
                pk=scheme.pk
            ).update(is_default=False)
        return ok(
            GradingSchemeSerializer(scheme, context={"request": request}).data,
            message="Grading scheme created.",
        )


class GradeBandViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = GradeBandSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = GradeBand.objects.select_related("scheme")
    school_lookup = "scheme__school"
    filterset_fields = ("scheme",)
    http_method_names = ["get", "patch", "delete", "head", "options"]
    write_roles = ("EXAM_ADMIN", "EXAM_ADMIN")


class DivisionBandViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = DivisionBandSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = DivisionBand.objects.select_related("scheme")
    school_lookup = "scheme__school"
    filterset_fields = ("scheme",)
    http_method_names = ["get", "patch", "delete", "head", "options"]
    write_roles = ("EXAM_ADMIN", "EXAM_ADMIN")
