from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission, can_manage_exam, accessible_school_ids
from apps.core.db import numeric_suffix
from apps.core.exceptions import BusinessRuleError
from apps.core.responses import ok
from apps.examinations.models import Examination

from .models import ExaminationCandidate
from .serializers import (
    EnrollCandidatesSerializer,
    EnrollListsSerializer,
    EnrollmentPreviewSerializer,
    ExaminationCandidateSerializer,
)
from .services import ExamEnrollmentService


def _scoped_examination(request, exam_id):
    schools = accessible_school_ids(request)
    exam = Examination.objects.filter(
        Q(school__in=schools) | Q(participating_schools__in=schools), pk=exam_id
    ).first()
    if exam is None:
        raise BusinessRuleError("Examination not found.", code="NOT_FOUND")
    return exam


class ExaminationCandidateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ExaminationCandidateSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("examination", "school", "status")
    search_fields = ("candidate_number", "candidate__first_name", "candidate__last_name")

    def get_queryset(self):
        schools = accessible_school_ids(self.request)
        return (
            ExaminationCandidate.objects.filter(
                Q(examination__school__in=schools)
                | Q(examination__participating_schools__in=schools)
            )
            .select_related("candidate", "school", "source_list", "examination")
            .distinct()
        ).annotate(num_seq=numeric_suffix("candidate__candidate_number")).order_by(
            "num_seq", "candidate__candidate_number"
        )

    @action(detail=False, methods=["post"], url_path="enroll-lists")
    def enroll_lists(self, request):
        serializer = EnrollListsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam = _scoped_examination(request, request.data.get("examination"))
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        stats = ExamEnrollmentService.enroll_lists(
            exam, serializer.validated_data["list_ids"], actor=request.user
        )
        return ok(stats, message="Candidates enrolled.")

    @action(detail=False, methods=["post"], url_path="enroll-candidates")
    def enroll_candidates(self, request):
        serializer = EnrollCandidatesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam = _scoped_examination(request, request.data.get("examination"))
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        stats = ExamEnrollmentService.enroll_candidates(
            exam, serializer.validated_data["candidate_ids"], actor=request.user
        )
        return ok(stats, message="Candidates enrolled.")

    @action(detail=False, methods=["post"], url_path="unenroll-list")
    def unenroll_list(self, request):
        """Bulk-unenroll every candidate of one list — ``{"examination", "list_id"}``.
        Enrollments with marks are withdrawn (kept for the record); the rest
        are deleted."""
        from apps.enrollment.models import ExaminationCandidate

        exam = _scoped_examination(request, request.data.get("examination"))
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        list_id = request.data.get("list_id")
        qs = ExaminationCandidate.objects.filter(
            examination=exam, source_list_id=list_id
        ).exclude(status=ExaminationCandidate.Status.WITHDRAWN)
        with_marks = set(
            qs.filter(marks__isnull=False).values_list("pk", flat=True)
        )
        removed, _ = qs.exclude(pk__in=with_marks).delete()
        withdrawn = qs.filter(pk__in=with_marks).update(
            status=ExaminationCandidate.Status.WITHDRAWN
        )
        log_action(
            actor=request.user, action="LIST_UNENROLLED", entity=exam,
            metadata={"list_id": str(list_id), "removed": removed, "withdrawn": withdrawn},
        )
        return ok(
            {"removed": removed, "withdrawn": withdrawn},
            message=(
                f"{removed} removed"
                + (f", {withdrawn} withdrawn (they have marks)." if withdrawn else ".")
            ),
        )

    @action(detail=False, methods=["post"], url_path="preview")
    def preview(self, request):
        serializer = EnrollmentPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam = _scoped_examination(request, request.data.get("examination"))
        preview = ExamEnrollmentService.preview(
            exam,
            list_ids=serializer.validated_data.get("list_ids"),
            candidate_ids=serializer.validated_data.get("candidate_ids"),
        )
        return ok(preview)

    @action(detail=True, methods=["post"], url_path="withdraw")
    def withdraw(self, request, pk=None):
        enrollment = self.get_object()
        if not can_manage_exam(request, enrollment.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        ExamEnrollmentService.withdraw(enrollment, actor=request.user)
        return ok(message="Candidate withdrawn.")

    @action(detail=True, methods=["delete"], url_path="remove")
    def remove(self, request, pk=None):
        enrollment = self.get_object()
        if not can_manage_exam(request, enrollment.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        ExamEnrollmentService.remove(enrollment, actor=request.user)
        return ok(message="Candidate removed.")
