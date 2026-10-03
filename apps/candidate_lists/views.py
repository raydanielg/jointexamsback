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


def _pdf_name(base, school_id):
    """Download filename — include the filtered school's name when set."""
    if not school_id:
        return f"{base}.pdf"
    import re

    from apps.schools.models import School

    name = (
        School.objects.filter(pk=school_id)
        .values_list("school_name", flat=True)
        .first()
    )
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name or "").strip("-")
    return f"{base}-{slug}.pdf" if slug else f"{base}.pdf"




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
        from apps.core.db import numeric_suffix

        qs = qs.annotate(num_seq=numeric_suffix("candidate_number")).order_by(
            "num_seq", "candidate_number"
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
        cell = ParagraphStyle("cell", fontSize=9, leading=11)
        rows = [["#", "Candidate number", "Full name", "School"]]
        for i, c in enumerate(qs, 1):
            rows.append([
                str(i), c.candidate_number,
                Paragraph(_html.escape(c.full_name), cell),
                Paragraph(_html.escape(c.school.school_name), cell),
            ])
        table = Table(
            rows, repeatRows=1,
            colWidths=[8 * mm, 32 * mm, 70 * mm, 70 * mm],
        )
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
        fname = _pdf_name(clist.name, school_id)
        response["Content-Disposition"] = f'inline; filename="{fname}"'
        return response

    @action(detail=True, methods=["get"], url_path="checklist-pdf")
    def checklist_pdf(self, request, pk=None):
        """Verification checklist PDF — number / name / school + empty
        Signature and Marks columns for manual marking. ``?school_id=``."""
        clist = self.get_object()
        qs = clist.candidates.select_related("school")
        school_id = request.query_params.get("school_id")
        if school_id:
            qs = qs.filter(school_id=school_id)
        from apps.core.db import numeric_suffix

        qs = qs.annotate(num_seq=numeric_suffix("candidate_number")).order_by(
            "num_seq", "candidate_number"
        )

        import html as _html
        import io

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
            buffer, pagesize=A4, topMargin=14 * mm, bottomMargin=14 * mm,
            leftMargin=12 * mm, rightMargin=12 * mm,
            title=f"{clist.name} — checklist",
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

        org_names = sorted({_html.escape(c.school.school_name.upper()) for c in qs})
        schools_line = (
            ", ".join(org_names[:-1]) + " &amp; " + org_names[-1]
            if len(org_names) > 1
            else (org_names[0] if org_names else "")
        )

        story = [
            Paragraph("THE PRIME MINISTER'S OFFICE", sub),
            Paragraph("REGIONAL ADMINISTRATION AND LOCAL GOVERNMENT", sub),
            Paragraph(schools_line, _fit(schools_line)),
            Paragraph(_html.escape(clist.name.upper()), head),
        ]
        meta = Table(
            [[
                Paragraph(_html.escape(clist.cohort or ""), sub),
                Paragraph("CANDIDATE CHECKLIST", head),
                Paragraph(
                    _html.escape(timezone.now().strftime("%B, %Y.").upper()), sub
                ),
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
            HRFlowable(width="100%", thickness=0.8, color=colors.black, spaceBefore=0, spaceAfter=8),
        ]

        # Small non-wrapping font so each row stays on one line.
        cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7, leading=8)
        cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")

        rows = [["#", "Candidate no.", "Full name", "School", "Signature", "Marks"]]
        for i, c in enumerate(qs, 1):
            rows.append([
                str(i),
                Paragraph(_html.escape(c.candidate_number), cell),
                Paragraph(_html.escape(c.full_name), cellb),
                Paragraph(_html.escape(c.school.school_name), cell),
                "",
                "",
            ])
        table = Table(
            rows, repeatRows=1,
            colWidths=[8 * mm, 32 * mm, 62 * mm, 44 * mm, 24 * mm, 16 * mm],
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94a3b8")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(table)
        story.append(Spacer(1, 10 * mm))
        story.append(Paragraph(
            "Invigilator: ______________________ "
            "Checked by: ______________________ "
            "Date: ____ / ____ / ________",
            sub,
        ))
        doc.build(story)
        buffer.seek(0)
        from django.http import FileResponse

        return FileResponse(
            buffer, as_attachment=False, content_type="application/pdf",
            filename=_pdf_name(f"{clist.name}-checklist", school_id),
        )

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

        counters = {}
        base = f"{prefix}/{year}/"

        def next_seq(key, candidate):
            """Clean sequential numbering over the selected candidates —
            ``start`` (default 1) is the first number, no gaps."""
            if key not in counters:
                counters[key] = start - 1
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
