from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission
from apps.core.exceptions import BusinessRuleError
from apps.core.responses import ok
from apps.examinations.models import Examination

from . import services


class AnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, RolePermission]

    class serializer_class(serializers.Serializer):  # noqa: D106
        pass

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        return ok(services.dashboard(request))

    @action(detail=False, methods=["get"], url_path="examination")
    def examination(self, request):
        exam = self._exam(request)
        return ok(services.exam_dashboard(exam, request))

    @action(detail=False, methods=["get"], url_path="examination-analytics")
    def examination_analytics(self, request):
        exam = self._exam(request)
        return ok(services.exam_analytics(exam))

    @action(detail=False, methods=["get"], url_path="compare")
    def compare(self, request):
        exam_a = self._exam(request, "exam_a")
        exam_b = self._exam(request, "exam_b")
        return ok(services.compare_examinations(exam_a, exam_b))

    def _exam(self, request, param="examination"):
        exam_id = request.query_params.get(param)
        if not exam_id:
            raise BusinessRuleError(f"{param} is required.", code="VALIDATION_ERROR")
        school = request.school
        exam = Examination.objects.filter(
            Q(school=school) | Q(participating_schools=school), pk=exam_id
        ).first()
        if exam is None:
            raise BusinessRuleError("Examination not found.", code="NOT_FOUND")
        return exam
