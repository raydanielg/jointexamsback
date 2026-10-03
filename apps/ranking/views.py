from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission, accessible_school_ids
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import ok
from apps.examinations.models import Examination, ExamSubject
from apps.results.models import CandidateExamResult, CandidateSubjectResult

from .models import RankingConfiguration
from .serializers import RankingConfigurationSerializer


def _scoped_exam(request, pk):
    schools = accessible_school_ids(request)
    exam = Examination.objects.filter(
        Q(school__in=schools) | Q(participating_schools__in=schools), pk=pk
    ).first()
    if exam is None:
        raise BusinessRuleError("Examination not found.", code="NOT_FOUND")
    return exam


class RankingConfigurationViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = RankingConfigurationSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = RankingConfiguration.objects.all()
    write_roles = ("EXAM_ADMIN",)


class RankingViewSet(viewsets.ViewSet):
    """Computed position listings sourced from result rows."""

    permission_classes = [IsAuthenticated, RolePermission]

    class serializer_class(serializers.Serializer):  # noqa: D106
        pass

    @action(detail=False, methods=["get"], url_path="examination")
    def examination(self, request):
        """Overall positions for one examination. Optional `school` filter
        scopes to a participating school."""
        exam = _scoped_exam(request, request.query_params.get("examination"))
        qs = (
            CandidateExamResult.objects.filter(examination=exam)
            .select_related("exam_candidate__candidate", "exam_candidate__school")
            .order_by("position")
        )
        if request.query_params.get("school"):
            qs = qs.filter(exam_candidate__school_id=request.query_params["school"])
        data = [
            {
                "exam_candidate_id": str(r.exam_candidate_id),
                "candidate_number": r.exam_candidate.candidate_number,
                "candidate_name": r.exam_candidate.candidate.full_name,
                "school": r.exam_candidate.school.school_name,
                "average_percentage": r.average_percentage,
                "total_score": r.total_score,
                "grade": r.grade,
                "division": r.division,
                "position": r.position,
                "school_position": r.school_position,
                "list_position": r.list_position,
                "status": r.status,
            }
            for r in qs[:1000]
        ]
        return ok(data)

    @action(detail=False, methods=["get"], url_path="subject")
    def subject(self, request):
        exam_subject = ExamSubject.objects.filter(
            pk=request.query_params.get("exam_subject")
        ).select_related("examination").first()
        if exam_subject is None:
            raise BusinessRuleError("exam_subject is required.", code="VALIDATION_ERROR")
        _scoped_exam(request, exam_subject.examination_id)
        qs = (
            CandidateSubjectResult.objects.filter(exam_subject=exam_subject)
            .select_related("exam_candidate__candidate", "exam_candidate__school")
            .order_by("position")
        )
        if request.query_params.get("school"):
            qs = qs.filter(exam_candidate__school_id=request.query_params["school"])
        data = [
            {
                "exam_candidate_id": str(r.exam_candidate_id),
                "candidate_number": r.exam_candidate.candidate_number,
                "candidate_name": r.exam_candidate.candidate.full_name,
                "school": r.exam_candidate.school.school_name,
                "percentage": r.percentage,
                "grade": r.grade,
                "position": r.position,
                "school_position": r.school_position,
                "status": r.status,
            }
            for r in qs[:1000]
        ]
        return ok(data)

    @action(detail=False, methods=["get"], url_path="schools")
    def schools(self, request):
        """School standings for a joint examination."""
        from .services import RankingService

        exam = _scoped_exam(request, request.query_params.get("examination"))
        results = list(
            CandidateExamResult.objects.filter(examination=exam)
            .select_related("exam_candidate")
        )
        standings = RankingService.school_standings(exam, results)
        school_names = {
            s.pk: s.school_name
            for s in exam.participating_schools.all()
        }
        school_names.setdefault(exam.school_id, exam.school.school_name)
        for row in standings:
            row["school_name"] = school_names.get(row["school_id"])
            row["school_id"] = str(row["school_id"])
        return ok(standings)
