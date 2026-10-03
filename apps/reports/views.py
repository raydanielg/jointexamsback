from django.db.models import Q
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.permissions import RolePermission, accessible_school_ids
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import created, ok
from apps.examinations.models import Examination

from .models import ReportJob, ReportTemplate
from .serializers import (
    CreateReportSerializer,
    ReportJobSerializer,
    ReportTemplateSerializer,
)
from .services import ReportGenerationService
from .tasks import generate_report_task


def _exam_for(request, exam_id):
    schools = accessible_school_ids(request)
    exam = Examination.objects.filter(
        Q(school__in=schools) | Q(participating_schools__in=schools), pk=exam_id
    ).first()
    if exam is None:
        raise BusinessRuleError("Examination not found.", code="NOT_FOUND")
    return exam


class ReportTemplateViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = ReportTemplateSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = ReportTemplate.objects.all()
    filterset_fields = ("report_type", "is_default")
    write_roles = ("EXAM_ADMIN",)


class ReportJobViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReportJobSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("report_type", "format", "status", "examination")
    write_roles = ("EXAM_ADMIN", "REPORT_VIEWER", "SCHOOL_COORDINATOR")
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report"

    def get_queryset(self):
        school = getattr(self.request, "school", None)
        if school is None:
            return ReportJob.objects.none()
        return ReportJob.objects.filter(
            Q(examination__school=school) | Q(examination__participating_schools=school)
        ).distinct().select_related("examination", "requested_by")

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        serializer = CreateReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam = _exam_for(request, serializer.validated_data["examination"])
        job = ReportGenerationService.create_job(
            request.school,
            serializer.validated_data["report_type"],
            serializer.validated_data["format"],
            examination=exam,
            params=serializer.validated_data.get("params"),
            actor=request.user,
        )
        if serializer.validated_data["run_async"]:
            generate_report_task.delay(str(job.pk))
        else:
            ReportGenerationService.generate(job)
        return created(
            ReportJobSerializer(job, context={"request": request}).data,
            message="Report queued.",
        )

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        if job.status != ReportJob.Status.COMPLETED or not job.file:
            raise BusinessRuleError("Report is not ready.", code="NOT_FOUND")
        from .renderers import CONTENT_TYPES

        response = HttpResponse(job.file.read(), content_type=CONTENT_TYPES[job.format])
        response["Content-Disposition"] = f'attachment; filename="{job.file.name.split("/")[-1]}"'
        return response

    @action(detail=False, methods=["get"], url_path="preview")
    def preview(self, request):
        """HTML preview rendered inline (non-file)."""
        from .builders import build_report_data
        from .renderers import render
        from .tabularize import tabularize

        exam = _exam_for(request, request.query_params.get("examination"))
        report_type = request.query_params.get("report_type", "EXAM_SUMMARY")
        data = build_report_data(exam, report_type, dict(request.query_params))
        doc = tabularize(data, report_type)
        return HttpResponse(render(doc, "HTML"), content_type="text/html; charset=utf-8")
