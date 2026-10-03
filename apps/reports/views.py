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

    @action(detail=False, methods=["get"], url_path="distribution")
    def distribution(self, request):
        """JSON grade distribution per school — A/B/C/D/F + ABSENT — for charts."""
        from collections import Counter

        from apps.enrollment.models import ExaminationCandidate
        from apps.results.models import CandidateExamResult, ResultStatus

        exam = _exam_for(request, request.query_params.get("examination"))
        results = (
            CandidateExamResult.objects.filter(examination=exam)
            .values_list("exam_candidate__school_id", "exam_candidate__school__school_name", "grade", "status")
        )
        enrolled = Counter(
            ExaminationCandidate.objects.filter(
                examination=exam,
                status__in=ExaminationCandidate.PARTICIPATING,
            ).values_list("school_id", "school__school_name")
        )
        grades = {}
        scored = {}
        for sid, sname, grade, status in results:
            key = (str(sid), sname)
            if status == ResultStatus.SCORED:
                grades.setdefault(key, Counter())[grade or "—"] += 1
                scored[key] = scored.get(key, 0) + 1

        letters = ["A", "B", "C", "D", "E", "F"]
        by_school = []
        overall = Counter()
        for (sid, sname), total in sorted(enrolled.items(), key=lambda kv: kv[0][1]):
            g = grades.get((sid, sname), Counter())
            absent = total - scored.get((sid, sname), 0)
            row = {"school_id": sid, "school": sname, "total": total, "ABSENT": absent}
            for letter in letters:
                row[letter] = g.get(letter, 0)
            for k in list(g):
                if k not in letters:
                    row.setdefault(k, g[k])
                    overall[k] += g[k]
            by_school.append(row)
            for letter in letters:
                overall[letter] += g.get(letter, 0)
            overall["ABSENT"] += absent

        return ok({
            "exam": {"id": str(exam.pk), "name": exam.name, "code": exam.code},
            "letters": letters + ["ABSENT"],
            "overall": dict(overall),
            "by_school": by_school,
        })

    @action(detail=False, methods=["get"], url_path="distribution-pdf")
    def distribution_pdf(self, request):
        """Inline PDF of the grade-distribution report for one examination."""
        from .builders import build_report_data
        from .renderers import render
        from .tabularize import tabularize

        exam = _exam_for(request, request.query_params.get("examination"))
        data = build_report_data(exam, "GRADE_DISTRIBUTION", dict(request.query_params))
        doc = tabularize(data, "GRADE_DISTRIBUTION")
        response = HttpResponse(render(doc, "PDF"), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="grade-distribution.pdf"'
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
