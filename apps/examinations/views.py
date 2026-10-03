from apps.accounts.permissions import accessible_school_ids
from django.db.models import Count, Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import (
    RolePermission,
    can_manage_exam,
    user_exam_membership,
)
from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import ok

from .models import ExamComponent, Examination, ExamSubject
from .serializers import (
    ExamComponentSerializer,
    ExaminationListSerializer,
    ExaminationSerializer,
    ExamSubjectSerializer,
)
from .services import ExaminationService, ExamSubjectService


def exam_scope_filter(request):
    """Q filter limiting examinations to those the active school organizes
    or participates in."""
    schools = accessible_school_ids(request)
    return Q(school__in=schools) | Q(participating_schools__in=schools)


class ExaminationViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("status", "term", "school")
    search_fields = ("name", "code")
    ordering_fields = ("start_date", "created_at", "name")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    school_lookup = None  # handled by get_queryset via exam_scope_filter

    write_roles = ("EXAM_ADMIN",)

    def get_serializer_class(self):
        if self.action == "list":
            return ExaminationListSerializer
        return ExaminationSerializer

    def get_queryset(self):
        qs = Examination.objects.filter(exam_scope_filter(self.request)).distinct()
        if self.action == "list":
            qs = qs.annotate(
                _candidate_count=Count(
                    "candidates",
                    filter=Q(candidates__status__in=("ACTIVE", "COMPLETED")),
                    distinct=True,
                ),
                _subject_count=Count(
                    "exam_subjects", filter=Q(exam_subjects__status="ACTIVE"), distinct=True
                ),
                _school_count=Count("participating_schools", distinct=True),
            ).select_related("school", "grading_scheme")
        return qs.select_related("school")

    def get_object(self):
        exam = super().get_object()
        role, _ = user_exam_membership(self.request, exam)
        if role is None:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You are not a member of a participating school for this examination.")
        return exam

    def check_exam_manager(self, exam):
        if not can_manage_exam(self.request, exam):
            raise BusinessRuleError(
                "Only organizer-school exam administrators may perform this action.",
                code="PERMISSION_DENIED",
            )

    def perform_create(self, serializer):
        exam = serializer.save()
        log_action(actor=self.request.user, action="EXAM_CREATE", entity=exam, request=self.request)

    def perform_update(self, serializer):
        exam = self.get_object()
        self.check_exam_manager(exam)
        if exam.status in (Examination.Status.PUBLISHED, Examination.Status.ARCHIVED):
            raise BusinessRuleError(
                "Published or archived examinations cannot be edited.", code="EXAM_LOCKED"
            )
        serializer.save()
        log_action(actor=self.request.user, action="EXAM_UPDATE", entity=exam, request=self.request)

    def perform_destroy(self, instance):
        self.check_exam_manager(instance)
        if instance.status != Examination.Status.DRAFT:
            raise BusinessRuleError(
                "Only draft examinations can be deleted.", code="EXAM_LOCKED"
            )
        log_action(actor=self.request.user, action="EXAM_DELETE", entity=instance, request=self.request)
        instance.delete()

    @action(detail=True, methods=["post"], url_path="transition")
    def transition(self, request, pk=None):
        exam = self.get_object()
        self.check_exam_manager(exam)
        new_status = request.data.get("status")
        if not new_status:
            raise BusinessRuleError("status is required.", code="VALIDATION_ERROR")
        exam = ExaminationService.transition(exam, new_status, actor=request.user)
        return ok(
            ExaminationSerializer(exam, context={"request": request}).data,
            message=f"Examination is now {exam.status}.",
        )

    @action(detail=True, methods=["get"], url_path="structure")
    def structure(self, request, pk=None):
        exam = self.get_object()
        return ok(ExaminationService.structure(exam))

    @action(detail=True, methods=["get"], url_path="candidates")
    def exam_candidates(self, request, pk=None):
        from apps.enrollment.models import ExaminationCandidate
        from apps.enrollment.serializers import ExaminationCandidateSerializer

        exam = self.get_object()
        qs = (
            ExaminationCandidate.objects.filter(examination=exam)
            .select_related("candidate", "school", "source_list")
            .order_by("school__school_code", "candidate_number")
        )
        school_id = request.query_params.get("school")
        if school_id:
            qs = qs.filter(school_id=school_id)
        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(candidate__first_name__icontains=search)
                | Q(candidate__last_name__icontains=search)
                | Q(candidate_number__icontains=search)
            )
        page = self.paginate_queryset(qs)
        serializer = ExaminationCandidateSerializer(page, many=True, context={"request": request})
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["get"], url_path="subjects")
    def exam_subjects(self, request, pk=None):
        exam = self.get_object()
        qs = exam.exam_subjects.select_related("subject").prefetch_related("components")
        return ok(ExamSubjectSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="components")
    def exam_components(self, request, pk=None):
        """Flat component listing across the exam's subjects; filterable by
        ?exam_subject=."""
        from .serializers import ExamComponentSerializer

        exam = self.get_object()
        qs = ExamComponent.objects.filter(exam_subject__examination=exam).select_related(
            "exam_subject__subject"
        ).order_by("exam_subject__display_order", "display_order")
        if request.query_params.get("exam_subject"):
            qs = qs.filter(exam_subject_id=request.query_params["exam_subject"])
        return ok(ExamComponentSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="dashboard")
    def dashboard(self, request, pk=None):
        from apps.analytics.services import exam_dashboard

        exam = self.get_object()
        return ok(exam_dashboard(exam, request))


class ExamSubjectViewSet(viewsets.ModelViewSet):
    serializer_class = ExamSubjectSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    write_roles = ("EXAM_ADMIN",)

    def get_queryset(self):
        schools = accessible_school_ids(self.request)
        qs = ExamSubject.objects.filter(
            Q(examination__school__in=schools)
            | Q(examination__participating_schools__in=schools)
        ).distinct()
        return qs.select_related("subject", "examination").prefetch_related("components")

    def _exam(self, serializer=None, obj=None):
        exam = (obj or self.get_object()).examination
        return exam

    def perform_create(self, serializer):
        exam_id = serializer.validated_data.get("examination") or self.request.data.get("examination")
        exam = Examination.objects.get(pk=exam_id.pk if hasattr(exam_id, "pk") else exam_id)
        if not can_manage_exam(self.request, exam):
            raise BusinessRuleError("Not authorized for this examination.", code="PERMISSION_DENIED")
        if exam.status != Examination.Status.DRAFT:
            raise BusinessRuleError("Subjects can only be added to DRAFT examinations.", code="EXAM_LOCKED")
        if ExamSubject.objects.filter(examination=exam, subject=serializer.validated_data["subject"]).exists():
            raise BusinessRuleError(
                "This subject is already part of the examination.", code="DUPLICATE"
            )
        exam_subject = serializer.save(examination=exam)
        ExamSubjectService.ensure_default_component(exam_subject)
        log_action(actor=self.request.user, action="EXAM_SUBJECT_ADD", entity=exam_subject, request=self.request)

    def perform_update(self, serializer):
        exam = self._exam()
        if not can_manage_exam(self.request, exam):
            raise BusinessRuleError("Not authorized for this examination.", code="PERMISSION_DENIED")
        if exam.status in (Examination.Status.FINALIZED, Examination.Status.PUBLISHED, Examination.Status.ARCHIVED):
            raise BusinessRuleError("Examination is finalized.", code="EXAM_LOCKED")
        serializer.save()
        ExamSubjectService.validate_component_configuration(serializer.instance)

    def perform_destroy(self, instance):
        exam = instance.examination
        if not can_manage_exam(self.request, exam):
            raise BusinessRuleError("Not authorized for this examination.", code="PERMISSION_DENIED")
        if exam.status != Examination.Status.DRAFT:
            raise BusinessRuleError(
                "Subjects can only be removed from DRAFT examinations.", code="EXAM_LOCKED"
            )
        if instance.components.filter(marks__isnull=False).exists():
            raise BusinessRuleError(
                "Cannot remove a subject that already has marks.", code="HAS_MARKS"
            )
        instance.delete()

    @action(detail=True, methods=["post"], url_path="lock-marks")
    def lock_marks(self, request, pk=None):
        exam_subject = self.get_object()
        if not can_manage_exam(request, exam_subject.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        exam_subject.marks_status = ExamSubject.MarksStatus.LOCKED
        exam_subject.save(update_fields=["marks_status", "updated_at"])
        log_action(actor=request.user, action="MARKS_LOCKED", entity=exam_subject, request=request)
        return ok(message="Marks locked.")

    @action(detail=True, methods=["post"], url_path="unlock-marks")
    def unlock_marks(self, request, pk=None):
        exam_subject = self.get_object()
        if not can_manage_exam(request, exam_subject.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        exam_subject.marks_status = ExamSubject.MarksStatus.OPEN
        exam_subject.save(update_fields=["marks_status", "updated_at"])
        log_action(actor=request.user, action="MARKS_UNLOCKED", entity=exam_subject, request=request)
        return ok(message="Marks unlocked.")


class ExamComponentViewSet(viewsets.ModelViewSet):
    serializer_class = ExamComponentSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    write_roles = ("EXAM_ADMIN",)

    def get_queryset(self):
        school = getattr(self.request, "school", None)
        if school is None:
            return ExamComponent.objects.none()
        return ExamComponent.objects.filter(
            Q(exam_subject__examination__school=school)
            | Q(exam_subject__examination__participating_schools=school)
        ).distinct().select_related("exam_subject", "exam_subject__subject")

    def _check(self, component_or_subject):
        es = component_or_subject if isinstance(component_or_subject, ExamSubject) else component_or_subject.exam_subject
        exam = es.examination
        if not can_manage_exam(self.request, exam):
            raise BusinessRuleError("Not authorized for this examination.", code="PERMISSION_DENIED")
        if exam.status in (Examination.Status.FINALIZED, Examination.Status.PUBLISHED, Examination.Status.ARCHIVED):
            raise BusinessRuleError("Examination is finalized.", code="EXAM_LOCKED")
        return es

    def perform_create(self, serializer):
        es_id = self.request.data.get("exam_subject")
        es = ExamSubject.objects.get(pk=es_id)
        self._check(es)
        component = serializer.save(exam_subject=es)
        ExamSubjectService.validate_component_configuration(es)
        log_action(actor=self.request.user, action="EXAM_COMPONENT_ADD", entity=component, request=self.request)

    def perform_update(self, serializer):
        self._check(serializer.instance)
        serializer.save()
        ExamSubjectService.validate_component_configuration(serializer.instance.exam_subject)

    def perform_destroy(self, instance):
        self._check(instance)
        if instance.marks.exists():
            raise BusinessRuleError(
                "Cannot remove a component that already has marks.", code="HAS_MARKS"
            )
        instance.delete()
