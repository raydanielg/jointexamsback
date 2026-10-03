import csv
import io
import logging
import uuid

import openpyxl
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError

from .models import ExportJob

logger = logging.getLogger("emas.exports")


def _rows_for_candidates(school, params):
    from apps.candidates.models import Candidate

    qs = Candidate.objects.filter(school=school).order_by("last_name", "first_name")
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    header = ["candidate_number", "first_name", "middle_name", "last_name", "gender",
              "phone", "guardian_name", "guardian_phone", "status"]
    rows = [
        [c.candidate_number, c.first_name, c.middle_name, c.last_name, c.gender,
         c.phone, c.guardian_name, c.guardian_phone, c.status]
        for c in qs.iterator()
    ]
    return header, rows


def _rows_for_candidate_list(school, params):
    from apps.candidate_lists.models import CandidateList

    lst = CandidateList.objects.filter(pk=params.get("candidate_list"), school=school).first()
    if lst is None:
        raise BusinessRuleError("Candidate list not found.", code="NOT_FOUND")
    header = ["candidate_number", "first_name", "middle_name", "last_name", "gender",
              "guardian_phone", "status"]
    rows = [
        [c.candidate_number, c.first_name, c.middle_name, c.last_name, c.gender,
         c.guardian_phone, c.status]
        for c in lst.candidates.order_by("last_name", "first_name").iterator()
    ]
    return header, rows


def _rows_for_marks(school, params):
    from apps.marks.models import Mark

    qs = (
        Mark.objects.filter(
            exam_candidate__examination__school=school
        )
        .select_related(
            "exam_candidate__candidate", "exam_candidate__examination",
            "exam_candidate__school", "component__exam_subject__subject",
        )
        .order_by("exam_candidate__candidate_number")
    )
    if params.get("examination"):
        qs = qs.filter(exam_candidate__examination_id=params["examination"])
    header = ["examination", "candidate_number", "candidate", "school",
              "subject", "component", "value", "status"]
    rows = [
        [m.exam_candidate.examination.code, m.exam_candidate.candidate_number,
         m.exam_candidate.candidate.full_name, m.exam_candidate.school.school_name,
         m.component.exam_subject.subject.name, m.component.code,
         str(m.value) if m.value is not None else "", m.status]
        for m in qs.iterator(chunk_size=2000)
    ]
    return header, rows


def _rows_for_results(school, params):
    from apps.results.models import CandidateExamResult

    qs = (
        CandidateExamResult.objects.filter(examination__school=school)
        .select_related(
            "exam_candidate__candidate", "exam_candidate__examination",
            "exam_candidate__school",
        )
        .order_by("position")
    )
    if params.get("examination"):
        qs = qs.filter(examination_id=params["examination"])
    header = ["examination", "candidate_number", "candidate", "school",
              "total", "average_%", "grade", "division", "position",
              "school_position", "status"]
    rows = [
        [r.examination.code, r.exam_candidate.candidate_number,
         r.exam_candidate.candidate.full_name, r.exam_candidate.school.school_name,
         str(r.total_score) if r.total_score is not None else "",
         str(r.average_percentage) if r.average_percentage is not None else "",
         r.grade, r.division, r.position or "", r.school_position or "", r.status]
        for r in qs.iterator(chunk_size=2000)
    ]
    return header, rows


def _rows_for_enrollments(school, params):
    from apps.enrollment.models import ExaminationCandidate

    qs = (
        ExaminationCandidate.objects.filter(examination__school=school)
        .select_related("candidate", "examination", "school", "source_list")
        .order_by("examination", "candidate_number")
    )
    if params.get("examination"):
        qs = qs.filter(examination_id=params["examination"])
    header = ["examination", "candidate_number", "candidate", "school",
              "source_list", "status"]
    rows = [
        [e.examination.code, e.candidate_number, e.candidate.full_name,
         e.school.school_name, e.source_list.name if e.source_list else "",
         e.status]
        for e in qs.iterator(chunk_size=2000)
    ]
    return header, rows


ROW_BUILDERS = {
    "CANDIDATES": _rows_for_candidates,
    "CANDIDATE_LIST": _rows_for_candidate_list,
    "MARKS": _rows_for_marks,
    "RESULTS": _rows_for_results,
    "ENROLLMENTS": _rows_for_enrollments,
}


class ExportService:
    @staticmethod
    def create_job(school, export_type, fmt, params=None, actor=None):
        if export_type not in ROW_BUILDERS:
            raise BusinessRuleError("Unknown export type.", code="VALIDATION_ERROR")
        job = ExportJob.objects.create(
            school=school, export_type=export_type, format=fmt,
            params=params or {}, requested_by=actor,
        )
        log_action(actor=actor, action="EXPORT_QUEUED", entity=job, school=school)
        return job

    @staticmethod
    def generate(job: ExportJob):
        job.status = ExportJob.Status.PROCESSING
        job.save(update_fields=["status", "updated_at"])
        try:
            header, rows = ROW_BUILDERS[job.export_type](job.school, job.params)
            payload = _render(header, rows, job.format)
            ext = "xlsx" if job.format == "XLSX" else "csv"
            job.file.save(
                f"{job.export_type.lower()}_{uuid.uuid4().hex[:8]}.{ext}",
                ContentFile(payload), save=False,
            )
            job.status = ExportJob.Status.COMPLETED
            job.completed_at = timezone.now()
            job.save(update_fields=["file", "status", "completed_at", "updated_at"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Export job %s failed", job.pk)
            job.status = ExportJob.Status.FAILED
            job.error = str(exc)[:2000]
            job.save(update_fields=["status", "error", "updated_at"])
            raise
        return job


def _render(header, rows, fmt):
    if fmt == "XLSX":
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(header)
        for row in rows:
            ws.append(row)
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")
