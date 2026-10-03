from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.accounts.permissions import RolePermission, can_manage_exam, user_exam_membership, accessible_school_ids
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import ok
from apps.enrollment.models import ExaminationCandidate
from apps.examinations.models import ExamComponent, Examination

from .models import (
    CandidateExamResult,
    CandidateSubjectResult,
    PredefinedComment,
    ResultCorrectionRequest,
    ResultSnapshot,
)
from .serializers import (
    CandidateExamResultSerializer,
    CandidateSubjectResultSerializer,
    CorrectionCreateSerializer,
    PredefinedCommentSerializer,
    ResultCorrectionRequestSerializer,
    ResultSnapshotDetailSerializer,
    ResultSnapshotSerializer,
    ReviewCorrectionSerializer,
)
from .services import (
    ResultCalculationService,
    ResultCorrectionService,
    ResultPublicationService,
)


def _exam_q(request):
    schools = accessible_school_ids(request)
    return Q(examination__school__in=schools) | Q(examination__participating_schools__in=schools)


def _get_exam(request, exam_id):
    schools = accessible_school_ids(request)
    exam = Examination.objects.filter(
        Q(school__in=schools) | Q(participating_schools__in=schools), pk=exam_id
    ).first()
    if exam is None:
        raise BusinessRuleError("Examination not found.", code="NOT_FOUND")
    return exam


class CandidateResultViewSet(viewsets.ReadOnlyModelViewSet):
    """Overall per-candidate examination results."""

    serializer_class = CandidateExamResultSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = (
        "examination", "exam_candidate", "exam_candidate__school",
        "grade", "division", "status",
    )
    search_fields = ("exam_candidate__candidate_number", "exam_candidate__candidate__last_name")

    def get_queryset(self):
        return (
            CandidateExamResult.objects.filter(_exam_q(self.request))
            .select_related(
                "exam_candidate__candidate", "exam_candidate__school", "examination"
            )
            .distinct()
        )

    @action(detail=False, methods=["get"], url_path="pdf")
    def pdf(self, request):
        """Printable results sheet — official masthead + results table.
        Params: ``examination`` (required), ``school_id``, ``q``, ``order``."""
        exam = _get_exam(request, request.query_params.get("examination"))
        qs = self.get_queryset().filter(examination=exam)
        qs = _apply_result_filters(qs, request.query_params)
        return _results_pdf_response(exam, qs)


class SubjectResultViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CandidateSubjectResultSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = (
        "examination", "exam_subject", "exam_candidate", "exam_candidate__school",
        "grade", "status",
    )

    def get_queryset(self):
        return (
            CandidateSubjectResult.objects.filter(_exam_q(self.request))
            .select_related(
                "exam_candidate__candidate", "exam_candidate__school",
                "exam_subject__subject",
            )
            .distinct()
        )


class ResultsWorkflowViewSet(viewsets.ViewSet):
    """Calculation / finalize / publish endpoints."""

    permission_classes = [IsAuthenticated, RolePermission]
    write_roles = ("EXAM_ADMIN",)

    class serializer_class(serializers.Serializer):  # noqa: D106
        pass

    @action(detail=False, methods=["post"], url_path="calculate")
    def calculate(self, request):
        exam = _get_exam(request, request.data.get("examination"))
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        run_async = request.data.get("async", False)
        if run_async:
            from .tasks import calculate_results_task

            calculate_results_task.delay(str(exam.pk), request.user.pk)
            return ok({"task": "queued"}, message="Result calculation queued.")
        stats = ResultCalculationService.calculate_exam(exam, actor=request.user)
        return ok(stats, message="Results calculated.")

    @action(detail=False, methods=["post"], url_path="finalize")
    def finalize(self, request):
        exam = _get_exam(request, request.data.get("examination"))
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        exam = ResultPublicationService.finalize(exam, actor=request.user)
        return ok({"status": exam.status}, message="Results finalized.")

    @action(detail=False, methods=["post"], url_path="publish")
    def publish(self, request):
        exam = _get_exam(request, request.data.get("examination"))
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        snapshot = ResultPublicationService.publish(exam, actor=request.user)
        return ok(
            {"status": exam.status, "snapshot_version": snapshot.version},
            message="Results published.",
        )

    @action(detail=False, methods=["post"], url_path="unpublish")
    def unpublish(self, request):
        exam = _get_exam(request, request.data.get("examination"))
        if not request.user.is_superadmin:
            raise BusinessRuleError(
                "Only a super administrator may unpublish results.", code="PERMISSION_DENIED"
            )
        exam.status = Examination.Status.FINALIZED
        exam.published_at = None
        exam.save(update_fields=["status", "published_at", "updated_at"])
        from apps.audit.services import log_action

        log_action(actor=request.user, action="RESULTS_UNPUBLISHED", entity=exam, request=request)
        return ok({"status": exam.status}, message="Results unpublished.")


class ResultSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("examination", "kind")

    def get_queryset(self):
        return ResultSnapshot.objects.filter(_exam_q(self.request)).distinct()

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ResultSnapshotDetailSerializer
        return ResultSnapshotSerializer


class ResultCorrectionRequestViewSet(viewsets.GenericViewSet):
    serializer_class = ResultCorrectionRequestSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    filterset_fields = ("examination", "status", "exam_candidate")
    write_roles = ("EXAM_ADMIN",)

    def get_queryset(self):
        return ResultCorrectionRequest.objects.filter(
            _exam_q(self.request)
        ).distinct().select_related(
            "exam_candidate__candidate", "component__exam_subject__subject",
            "requested_by", "reviewed_by",
        )

    def list(self, request):
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(
            self.get_serializer(page, many=True).data
        )

    def retrieve(self, request, pk=None):
        return ok(self.get_serializer(self.get_object()).data)

    @action(detail=False, methods=["post"], url_path="request")
    def create_request(self, request):
        serializer = CorrectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam = _get_exam(request, request.data.get("examination"))
        if not can_manage_exam(request, exam):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        candidate = ExaminationCandidate.objects.filter(
            pk=serializer.validated_data["exam_candidate"], examination=exam
        ).first()
        component = ExamComponent.objects.filter(
            pk=serializer.validated_data["component"],
            exam_subject__examination=exam,
        ).first()
        if not candidate or not component:
            raise BusinessRuleError("Candidate or component not found.", code="NOT_FOUND")
        correction = ResultCorrectionService.create_request(
            exam, candidate, component,
            requested_value=serializer.validated_data.get("requested_value"),
            requested_status=serializer.validated_data.get("requested_status", ""),
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return ok(ResultCorrectionRequestSerializer(correction).data, message="Correction requested.")

    @action(detail=True, methods=["post"], url_path="review")
    def review(self, request, pk=None):
        correction = self.get_object()
        if not can_manage_exam(request, correction.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        serializer = ReviewCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        correction = ResultCorrectionService.review(
            correction,
            actor=request.user,
            approve=serializer.validated_data["approve"],
            note=serializer.validated_data.get("note", ""),
        )
        return ok(ResultCorrectionRequestSerializer(correction).data, message="Reviewed.")

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        correction = self.get_object()
        if not can_manage_exam(request, correction.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        correction = ResultCorrectionService.review(correction, actor=request.user, approve=True)
        return ok(ResultCorrectionRequestSerializer(correction).data, message="Approved.")

    @action(detail=True, methods=["post"], url_path="apply")
    def apply(self, request, pk=None):
        correction = self.get_object()
        if not can_manage_exam(request, correction.examination):
            raise BusinessRuleError("Not authorized.", code="PERMISSION_DENIED")
        correction = ResultCorrectionService.apply(correction, actor=request.user)
        return ok(ResultCorrectionRequestSerializer(correction).data, message="Correction applied.")


class PredefinedCommentViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = PredefinedCommentSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = PredefinedComment.objects.all()
    write_roles = ("EXAM_ADMIN", "SCHOOL_COORDINATOR")


def _apply_result_filters(qs, params):
    """school_id / q / order filters shared by the JSON + PDF endpoints."""
    school_id = params.get("school_id")
    if school_id:
        qs = qs.filter(exam_candidate__school_id=school_id)
    search = params.get("q")
    if search:
        qs = qs.filter(
            Q(exam_candidate__candidate__first_name__icontains=search)
            | Q(exam_candidate__candidate__middle_name__icontains=search)
            | Q(exam_candidate__candidate__last_name__icontains=search)
            | Q(exam_candidate__candidate__candidate_number__icontains=search)
        )
    order = params.get("order", "position_asc")
    if order == "position_desc":
        return qs.order_by("-position", "exam_candidate__candidate__candidate_number")
    if order == "name_asc":
        return qs.order_by(
            "exam_candidate__candidate__first_name",
            "exam_candidate__candidate__last_name",
        )
    return qs.order_by("position", "exam_candidate__candidate__candidate_number")


def _public_result_rows(qs):
    return [
        {
            "candidate_number": r.exam_candidate.candidate.candidate_number,
            "full_name": r.exam_candidate.candidate.full_name,
            "school_name": r.exam_candidate.school.school_name,
            "school_id": str(r.exam_candidate.school_id),
            "score": str(r.total_score) if r.total_score is not None else None,
            "grade": r.grade,
            "points": str(r.points_sum) if r.points_sum is not None else None,
            "division": r.division,
            "position": r.position,
        }
        for r in qs
    ]


def _results_pdf_response(exam, qs):
    import html as _html
    import io

    from django.http import FileResponse
    from django.utils import timezone
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Table, TableStyle,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm, title=exam.name,
    )
    head = ParagraphStyle(
        "masthead", fontName="Helvetica-Bold", fontSize=11,
        leading=15, alignment=TA_CENTER,
    )
    sub = ParagraphStyle("masthead-sub", parent=head, fontSize=9, leading=12)

    def _fit(text: str) -> ParagraphStyle:
        if len(text) <= 90:
            return head
        size = max(6.5, 11 * 90 / len(text))
        return ParagraphStyle(
            "masthead-fit", parent=head, fontSize=size, leading=size + 2
        )

    org_names = sorted(
        {_html.escape(r.exam_candidate.school.school_name.upper()) for r in qs}
    )
    if len(org_names) > 1:
        schools_line = ", ".join(org_names[:-1]) + " &amp; " + org_names[-1]
    else:
        schools_line = (
            org_names[0] if org_names
            else _html.escape(exam.school.school_name.upper())
        )

    story = [
        Paragraph("THE PRIME MINISTER'S OFFICE", sub),
        Paragraph("REGIONAL ADMINISTRATION AND LOCAL GOVERNMENT", sub),
        Paragraph(schools_line, _fit(schools_line)),
        Paragraph(
            _html.escape(exam.name.upper()),
            _fit(_html.escape(exam.name.upper())),
        ),
    ]
    meta = Table(
        [[
            Paragraph(_html.escape(exam.code), sub),
            Paragraph("EXAMINATION RESULTS", head),
            Paragraph(_html.escape(timezone.now().strftime("%B, %Y.").upper()), sub),
        ]],
        colWidths=["33%", "34%", "33%"],
    )
    meta.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [
        meta,
        HRFlowable(width="100%", thickness=2.5, color=colors.black, spaceBefore=2, spaceAfter=1),
        HRFlowable(width="100%", thickness=0.8, color=colors.black, spaceBefore=0, spaceAfter=10),
    ]

    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.5, leading=9)
    cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")

    single_subject = exam.exam_subjects.count() == 1
    last_col = "PTS" if single_subject else "Div"
    rows = [["Candidate no.", "Full name", "School", "Score", "Gr", last_col]]
    for r in qs:
        tail = (
            f"{r.points_sum:g}" if r.points_sum is not None else "—"
        ) if single_subject else (r.division or "—")
        rows.append([
            Paragraph(_html.escape(r.exam_candidate.candidate.candidate_number), cell),
            Paragraph(_html.escape(r.exam_candidate.candidate.full_name), cellb),
            Paragraph(_html.escape(r.exam_candidate.school.school_name), cell),
            str(r.total_score or "—"),
            r.grade or "—",
            tail,
        ])
    table = Table(
        rows, repeatRows=1,
        colWidths=[30 * mm, 65 * mm, 48 * mm, 18 * mm, 11 * mm, 12 * mm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("ALIGN", (3, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return FileResponse(
        buffer, as_attachment=False, content_type="application/pdf",
        filename=f"{exam.code}-results.pdf",
    )


class PublicResultsView(viewsets.ViewSet):
    """Unauthenticated read-only results for PUBLISHED examinations —
    parents/schools reach them via the per-exam public token."""

    permission_classes = [AllowAny]

    def _exam(self, token):
        exam = Examination.objects.filter(
            pk__isnull=False, public_token=token,
            status=Examination.Status.PUBLISHED,
        ).first()
        if exam is None:
            raise BusinessRuleError("Results not found.", code="NOT_FOUND")
        return exam

    def _qs(self, exam):
        return (
            CandidateExamResult.objects.filter(examination=exam)
            .select_related(
                "exam_candidate__candidate", "exam_candidate__school", "examination"
            )
        )

    def list(self, request, token=None):
        exam = self._exam(token)
        qs = _apply_result_filters(self._qs(exam), request.query_params)
        schools = sorted(
            {
                (str(r.exam_candidate.school_id), r.exam_candidate.school.school_name)
                for r in self._qs(exam)
            },
            key=lambda t: t[1],
        )
        return ok({
            "exam": {
                "id": str(exam.pk), "name": exam.name, "code": exam.code,
                "term": exam.term, "period": exam.period,
            },
            "schools": [
                {"id": sid, "name": name} for sid, name in schools
            ],
            "single_subject": exam.exam_subjects.count() == 1,
            "results": _public_result_rows(qs),
        })

    @action(detail=False, methods=["get"], url_path="pdf")
    def pdf(self, request, token=None):
        exam = self._exam(token)
        qs = _apply_result_filters(self._qs(exam), request.query_params)
        return _results_pdf_response(exam, qs)

