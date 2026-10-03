import csv

from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import RolePermission
from apps.audit.services import log_action
from apps.core.mixins import SchoolScopedQuerySetMixin
from apps.core.responses import ok
from apps.core.exceptions import BusinessRuleError

from .models import CandidateList, CandidateListEntry
from .serializers import (
    CandidateListSerializer,
    CandidateListMembersSerializer,
    DuplicateListSerializer,
)
from .services import CandidateListService


class CandidateListViewSet(SchoolScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = CandidateListSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    queryset = CandidateList.objects.select_related("school", "created_by")
    filterset_fields = ("status", "cohort", "school")
    search_fields = ("name", "description", "cohort")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    write_roles = ("EXAM_ADMIN", "SCHOOL_COORDINATOR")

    def perform_destroy(self, instance):
        if instance.exam_candidates.exists():
            raise BusinessRuleError(
                "List has enrollments in examinations; archive it instead.",
                code="LIST_IN_USE",
            )
        instance.delete()

    @action(detail=True, methods=["post"], url_path="add-candidates")
    def add_candidates(self, request, pk=None):
        clist = self.get_object()
        serializer = CandidateListMembersSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        added = CandidateListService.add_candidates(
            clist, serializer.validated_data["candidate_ids"],
            actor=request.user, request=request,
        )
        return ok({"added": added, "total": clist.entries.count()}, message="Candidates added.")

    @action(detail=True, methods=["post"], url_path="remove-candidates")
    def remove_candidates(self, request, pk=None):
        clist = self.get_object()
        serializer = CandidateListMembersSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        removed = CandidateListService.remove_candidates(
            clist, serializer.validated_data["candidate_ids"], actor=request.user
        )
        return ok({"removed": removed, "total": clist.entries.count()}, message="Candidates removed.")

    @action(detail=True, methods=["get"], url_path="candidates")
    def list_candidates(self, request, pk=None):
        clist = self.get_object()
        qs = clist.candidates.select_related("school")
        # Namespaced params — plain "school"/"search" would be consumed by
        # DRF filter backends on the *list* queryset inside get_object().
        school_id = request.query_params.get("school_id")
        if school_id:
            qs = qs.filter(school_id=school_id)
        search = request.query_params.get("q")
        if search:
            from django.db.models import Q

            qs = qs.filter(
                Q(first_name__icontains=search)
                | Q(middle_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(candidate_number__icontains=search)
            )
        page = self.paginate_queryset(qs)
        from apps.candidates.serializers import CandidateSerializer

        serializer = CandidateSerializer(page, many=True, context={"request": request})
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["get"], url_path="export")
    def export(self, request, pk=None):
        clist = self.get_object()
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{clist.name}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["candidate_number", "first_name", "middle_name", "last_name",
             "gender", "phone", "guardian_name", "guardian_phone", "status"]
        )
        for c in clist.candidates.iterator():
            writer.writerow([
                c.candidate_number, c.first_name, c.middle_name, c.last_name,
                c.gender, c.phone, c.guardian_name, c.guardian_phone, c.status,
            ])
        return response

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        """Printable PDF of the list's candidates — ``?school=`` filters to one
        organization, ``?search=`` narrows by name/number."""
        clist = self.get_object()
        order = request.query_params.get("order", "number_asc")
        fields = {
            "name_asc": ["first_name", "middle_name", "last_name"],
            "name_desc": ["-first_name", "-middle_name", "-last_name"],
            "number_asc": ["num_seq", "candidate_number"],
            "number_desc": ["-num_seq", "-candidate_number"],
        }.get(order, ["num_seq", "candidate_number"])
        from apps.core.db import numeric_suffix

        qs = clist.candidates.select_related("school").annotate(
            num_seq=numeric_suffix()
        ).order_by(*fields)
        school_id = request.query_params.get("school_id")
        if school_id:
            qs = qs.filter(school_id=school_id)
        search = request.query_params.get("q")
        if search:
            from django.db.models import Q

            qs = qs.filter(
                Q(first_name__icontains=search)
                | Q(middle_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(candidate_number__icontains=search)
            )

        import io

        import html as _html

        from django.utils import timezone

        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm,
            leftMargin=15 * mm, rightMargin=15 * mm, title=clist.name,
        )

        # Official Tanzanian exam-paper masthead — centered, bold, uppercase,
        # with a double rule underneath.
        head = ParagraphStyle(
            "masthead", fontName="Helvetica-Bold", fontSize=11,
            leading=15, alignment=TA_CENTER,
        )
        sub = ParagraphStyle(
            "masthead-sub", parent=head, fontSize=9, leading=12,
        )

        def _fit(text: str) -> ParagraphStyle:
            """Shrink the masthead font for long school/exam lines so they
            never overflow the page width (~95 chars at 11pt)."""
            if len(text) <= 90:
                return head
            size = max(6.5, 11 * 90 / len(text))
            return ParagraphStyle(
                "masthead-fit", parent=head, fontSize=size, leading=size + 2
            )

        org_names = sorted({_html.escape(c.school.school_name.upper()) for c in qs})
        if len(org_names) > 1:
            schools_line = ", ".join(org_names[:-1]) + " &amp; " + org_names[-1]
        else:
            schools_line = org_names[0] if org_names else clist.school.school_name.upper()

        story = [
            Paragraph("THE PRIME MINISTER'S OFFICE", sub),
            Paragraph("REGIONAL ADMINISTRATION AND LOCAL GOVERNMENT", sub),
            Paragraph(schools_line, head),
            Paragraph(_html.escape(clist.name.upper()), head),
        ]
        meta = Table(
            [[
                Paragraph(_html.escape(clist.school.school_code), sub),
                Paragraph("CANDIDATE LIST", head),
                Paragraph(timezone.now().strftime("%B, %Y.").upper(), sub),
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
        rows = [["#", "Candidate number", "Full name", "School"]]
        for i, c in enumerate(qs, 1):
            rows.append([
                str(i), c.candidate_number, _html.escape(c.full_name), _html.escape(c.school.school_name),
            ])
        table = Table(rows, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6fa")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d2dd")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
        doc.build(story)
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{clist.name}.pdf"'
        return response

    @action(detail=True, methods=["post"], url_path="generate-numbers")
    def generate_numbers(self, request, pk=None):
        """Renumber the list's candidates ``PREFIX/YEAR/0001`` — per school,
        continuing after the highest number already used for that prefix."""
        clist = self.get_object()
        prefix = (request.data.get("prefix") or "JNT").strip() or "JNT"
        try:
            year = int(request.data.get("year") or clist.cohort or 0) or None
        except (TypeError, ValueError):
            year = None
        if year is None:
            from django.utils import timezone

            year = timezone.now().year
        mode = request.data.get("mode", "per_school")
        order = request.data.get("order", "name_asc")
        try:
            start = max(0, int(request.data.get("start") or 0))
        except (TypeError, ValueError):
            start = 0
        fields = {
            "name_asc": ["first_name", "middle_name", "last_name"],
            "name_desc": ["-first_name", "-middle_name", "-last_name"],
            "number_asc": ["candidate_number"],
            "number_desc": ["-candidate_number"],
        }.get(order, ["first_name", "middle_name", "last_name"])
        if mode == "combined":
            qs = clist.candidates.select_related("school").order_by(*fields)
        else:
            qs = clist.candidates.select_related("school").order_by(
                "school__school_code", *fields
            )
        school_id = request.data.get("school")
        if school_id:
            qs = qs.filter(school_id=school_id)

        from apps.candidates.models import Candidate

        counters = {}
        base = f"{prefix}/{year}/"
        base_school_ids = set(qs.values_list("school_id", flat=True))
        pool = Candidate.objects.filter(school_id__in=base_school_ids)

        def next_seq(key, candidate):
            """Continue after the highest ``prefix/year/`` number already used —
            scoped per school for ``per_school``, across the selection for
            ``combined``."""
            if key not in counters:
                scope = (
                    pool if mode == "combined"
                    else pool.filter(school_id=candidate.school_id)
                )
                last = (
                    scope.filter(candidate_number__startswith=base)
                    .exclude(pk=candidate.pk)
                    .order_by("-candidate_number")
                    .values_list("candidate_number", flat=True)
                    .first()
                )
                seq = 0
                if last:
                    try:
                        seq = int(last.rsplit("/", 1)[-1])
                    except (ValueError, IndexError):
                        seq = 0
                # explicit start wins over continuing from the highest number
                counters[key] = (start - 1) if start else seq
            counters[key] += 1
            return counters[key]

        updated = 0
        for c in qs.iterator():
            key = 0 if mode == "combined" else c.school_id
            c.candidate_number = f"{base}{next_seq(key, c):04d}"
            c.save(update_fields=["candidate_number", "updated_at"])
            updated += 1
        log_action(
            actor=request.user, action="LIST_RENUMBER", entity=clist,
            school=clist.school,
            metadata={"prefix": prefix, "year": year, "updated": updated},
        )
        return ok({"updated": updated}, message=f"Generated {updated} candidate numbers.")

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        clist = self.get_object()
        serializer = DuplicateListSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        copy = CandidateListService.duplicate(
            clist, serializer.validated_data["new_name"], actor=request.user
        )
        return ok(CandidateListSerializer(copy, context={"request": request}).data,
                  message="List duplicated.")

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        clist = self.get_object()
        clist.status = CandidateList.Status.ARCHIVED
        clist.save(update_fields=["status", "updated_at"])
        log_action(actor=request.user, action="LIST_ARCHIVED", entity=clist, request=request)
        return ok(CandidateListSerializer(clist, context={"request": request}).data,
                  message="List archived.")
