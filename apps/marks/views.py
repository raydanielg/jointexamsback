import uuid

from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.models import Role
from apps.accounts.permissions import RolePermission, resolve_active_school, user_exam_membership, accessible_school_ids
from apps.core.exceptions import BusinessRuleError
from apps.core.responses import ok
from apps.enrollment.models import ExaminationCandidate
from apps.examinations.models import ExamComponent, ExamSubject
from apps.examinations.services import ExamSubjectService

from .models import Mark, MarkChangeLog, MarkComment
from .services import MarksService
from .serializers import (
    BulkMarksSerializer,
    MarkChangeLogSerializer,
    MarkCommentSerializer,
    MarkEntryRowSerializer,
    MarkSerializer,
)


def _scoped_exam_subjects(request):
    schools = accessible_school_ids(request)
    return ExamSubject.objects.filter(
        Q(examination__school__in=schools)
        | Q(examination__participating_schools__in=schools)
    ).distinct()


def _marks_candidates_scope(request, qs):
    """MARKS_ENTRY members may only touch their own school's candidates."""
    role = getattr(request.membership, "role", None)
    if request.user.is_superadmin or role != Role.MARKS_ENTRY:
        return qs
    return qs.filter(exam_candidate__school__in=accessible_school_ids(request))


class MarkViewSet(viewsets.ReadOnlyModelViewSet):
    """Read access to marks plus marks-entry actions."""

    serializer_class = MarkSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("status", "component", "exam_candidate")
    write_roles = ("EXAM_ADMIN", "MARKS_ENTRY")

    def get_queryset(self):
        school = getattr(self.request, "school", None)
        if school is None:
            return Mark.objects.none()
        qs = Mark.objects.filter(
            Q(exam_candidate__examination__school=school)
            | Q(exam_candidate__examination__participating_schools=school)
        ).distinct()
        qs = _marks_candidates_scope(self.request, qs)
        return qs.select_related(
            "exam_candidate__candidate", "exam_candidate__school",
            "component__exam_subject__subject", "entered_by",
        )

    @action(detail=False, methods=["get"], url_path="sheet")
    def sheet(self, request):
        """Marks-entry sheet: all enrolled candidates x one component (or
        all components of one exam subject)."""
        exam_subject_id = request.query_params.get("exam_subject")
        if not exam_subject_id:
            raise BusinessRuleError("exam_subject is required.", code="VALIDATION_ERROR")
        exam_subject = _scoped_exam_subjects(request).filter(pk=exam_subject_id).first()
        if exam_subject is None:
            raise BusinessRuleError("Examination subject not found.", code="NOT_FOUND")

        components = list(ExamSubjectService.active_components(exam_subject))
        marks = {
            (m.exam_candidate_id, m.component_id): m
            for m in Mark.objects.filter(
                component__in=components,
                exam_candidate__examination_id=exam_subject.examination_id,
            )
        }
        enrollments = ExaminationCandidate.objects.filter(
            examination_id=exam_subject.examination_id,
            status__in=ExaminationCandidate.PARTICIPATING,
        ).select_related("candidate", "school")

        # Numeric order — the trailing digits of the candidate number,
        # regardless of school/prefix (JNT/2026/0001 → … → 0500).
        from apps.core.db import numeric_suffix

        enrollments = enrollments.annotate(
            num_seq=numeric_suffix("candidate__candidate_number")
        ).order_by("num_seq", "candidate__candidate_number")
        role = getattr(request.membership, "role", None)
        if role == Role.MARKS_ENTRY and not request.user.is_superadmin:
            enrollments = enrollments.filter(school__in=accessible_school_ids(request))

        rows = []
        for enrollment in enrollments.iterator():
            for component in components:
                mark = marks.get((enrollment.pk, component.pk))
                rows.append(
                    {
                        "exam_candidate_id": enrollment.pk,
                        "candidate_number": enrollment.candidate.candidate_number,
                        "candidate_name": enrollment.candidate.full_name,
                        "school_name": enrollment.school.school_name,
                        "component_id": component.pk,
                        "component_code": component.code,
                        "component_name": component.name,
                        "maximum_marks": component.maximum_marks,
                        "mark_id": mark.pk if mark else None,
                        "value": mark.value if mark else None,
                        "status": mark.status if mark else Mark.Status.PENDING,
                        "remarks": mark.remarks if mark else "",
                    }
                )
        return ok({"rows": rows, "completion": MarksService.completion(exam_subject)})

    @action(detail=False, methods=["post"], url_path="entry")
    def entry(self, request):
        """Single mark entry."""
        serializer = MarkEntryRowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        exam_subject = _scoped_exam_subjects(request).filter(
            pk=request.data.get("exam_subject")
        ).first()
        if exam_subject is None:
            raise BusinessRuleError("exam_subject is required.", code="VALIDATION_ERROR")

        enrollment = _resolve_enrollment(exam_subject, data, request)
        component = _resolve_component(exam_subject, data)

        mark = MarksService.set_mark(
            enrollment,
            component,
            value=data.get("value"),
            status=data.get("status"),
            actor=request.user,
            reason=data.get("reason", ""),
            request=request,
        )
        return ok(MarkSerializer(mark).data, message="Mark saved.")

    @action(detail=False, methods=["post"], url_path="bulk-entry")
    def bulk_entry(self, request):
        serializer = BulkMarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam_subject = _scoped_exam_subjects(request).filter(
            pk=request.data.get("exam_subject")
        ).first()
        if exam_subject is None:
            raise BusinessRuleError("exam_subject is required.", code="VALIDATION_ERROR")
        result = MarksService.bulk_set(
            exam_subject, serializer.validated_data["rows"],
            actor=request.user, request=request,
        )
        if not result["success"]:
            raise BusinessRuleError(
                "Bulk marks entry aborted.", code="MARKS_VALIDATION_FAILED",
                details={"errors": result["errors"]},
            )
        return ok(result, message=f"{result['entered']} marks saved.")

    @action(detail=False, methods=["get"], url_path="completion")
    def completion(self, request):
        exam_subject = _scoped_exam_subjects(request).filter(
            pk=request.query_params.get("exam_subject")
        ).first()
        if exam_subject is None:
            raise BusinessRuleError("exam_subject is required.", code="VALIDATION_ERROR")
        return ok(MarksService.completion(exam_subject))


def _resolve_enrollment(exam_subject, data, request):
    qs = ExaminationCandidate.objects.filter(
        examination=exam_subject.examination,
        status__in=(ExaminationCandidate.Status.ACTIVE, ExaminationCandidate.Status.COMPLETED),
    )
    role = getattr(request.membership, "role", None)
    if role == Role.MARKS_ENTRY and not request.user.is_superadmin:
        qs = qs.filter(school__in=accessible_school_ids(request))
    if data.get("exam_candidate"):
        enrollment = qs.filter(pk=data["exam_candidate"]).first()
    elif data.get("candidate_number"):
        enrollment = qs.filter(candidate_number=data["candidate_number"]).first()
    else:
        enrollment = None
    if enrollment is None:
        raise BusinessRuleError("Candidate is not enrolled in this examination.", code="NOT_ENROLLED")
    return enrollment


def _resolve_component(exam_subject, data):
    components = list(ExamSubjectService.active_components(exam_subject))
    key = data.get("component") or data.get("component_code")
    if key:
        try:
            pk = uuid.UUID(str(key))
            component = next((c for c in components if c.pk == pk), None)
        except ValueError:
            component = next(
                (c for c in components if c.code.lower() == str(key).lower()), None
            )
    else:
        component = components[0] if len(components) == 1 else None
    if component is None:
        raise BusinessRuleError(
            "Component not found or ambiguous; specify component.",
            code="VALIDATION_ERROR",
        )
    return component


class MarkChangeLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MarkChangeLogSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("exam_candidate", "component", "action")

    def get_queryset(self):
        school = getattr(self.request, "school", None)
        if school is None:
            return MarkChangeLog.objects.none()
        return MarkChangeLog.objects.filter(
            Q(exam_candidate__examination__school=school)
            | Q(exam_candidate__examination__participating_schools=school)
        ).distinct().select_related(
            "exam_candidate", "component__exam_subject__subject", "changed_by"
        )


class MarkCommentViewSet(viewsets.ModelViewSet):
    serializer_class = MarkCommentSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("examination", "exam_candidate", "exam_subject", "scope")
    write_roles = ("EXAM_ADMIN", "SCHOOL_COORDINATOR")

    def get_queryset(self):
        school = getattr(self.request, "school", None)
        if school is None:
            return MarkComment.objects.none()
        return MarkComment.objects.filter(
            Q(examination__school=school) | Q(examination__participating_schools=school)
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
