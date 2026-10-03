import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError
from apps.enrollment.models import ExaminationCandidate
from apps.examinations.models import ExamComponent, Examination, ExamSubject

from .models import Mark, MarkChangeLog

logger = logging.getLogger("emas.marks")

NUMERIC_STATUSES = (Mark.Status.ENTERED,)
NO_VALUE_STATUSES = (
    Mark.Status.ABSENT,
    Mark.Status.EXEMPT,
    Mark.Status.MISSING,
    Mark.Status.PENDING,
)


class MarksValidationService:
    """Pure validation: value ranges vs component maximum."""

    @staticmethod
    def validate_value(component: ExamComponent, value):
        if value is None:
            return
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise BusinessRuleError(
                f"Invalid mark value '{value}'.", code="INVALID_MARK"
            )
        if numeric < 0 or numeric > component.maximum_marks:
            raise BusinessRuleError(
                f"Mark must be between 0 and {component.maximum_marks} "
                f"for {component.name}.",
                code="MARK_OUT_OF_RANGE",
                details={"component": component.code, "max": str(component.maximum_marks)},
            )
        return numeric


class MarksService:
    """Mark entry and audit. All writes go through here."""

    @staticmethod
    def _check_marks_open(exam_subject: ExamSubject, force=False):
        if exam_subject.marks_status == ExamSubject.MarksStatus.LOCKED and not force:
            raise BusinessRuleError(
                "Marks for this subject are locked. Unlock them to make changes.",
                code="MARKS_LOCKED",
            )
        exam_status = (
            Examination.objects.filter(pk=exam_subject.examination_id)
            .values_list("status", flat=True)
            .first()
        )
        if exam_status in (
            Examination.Status.FINALIZED,
            Examination.Status.PUBLISHED,
            Examination.Status.ARCHIVED,
        ) and not force:
            raise BusinessRuleError(
                "Marks cannot be changed on a finalized or published examination. "
                "Use the correction workflow.",
                code="EXAM_PUBLISHED",
            )

    @staticmethod
    @transaction.atomic
    def set_mark(exam_candidate: ExaminationCandidate, component: ExamComponent, *,
                 value=None, status=None, actor=None, reason="", force=False, request=None):
        if exam_candidate.examination_id != component.exam_subject.examination_id:
            raise BusinessRuleError(
                "Candidate is not enrolled in this examination.", code="NOT_ENROLLED"
            )
        if exam_candidate.status in (
            ExaminationCandidate.Status.WITHDRAWN,
            ExaminationCandidate.Status.ABSENT,
            ExaminationCandidate.Status.DISQUALIFIED,
        ) and not force:
            raise BusinessRuleError(
                f"Cannot enter marks for a {exam_candidate.status} candidate.",
                code="CANDIDATE_INACTIVE",
            )
        MarksService._check_marks_open(component.exam_subject, force=force)

        numeric = None
        if status in NO_VALUE_STATUSES:
            mark_status = status
        elif value is not None or status in (None, Mark.Status.ENTERED):
            numeric = MarksValidationService.validate_value(component, value)
            if numeric is None:
                raise BusinessRuleError("A mark value or a status is required.", code="VALIDATION_ERROR")
            mark_status = Mark.Status.ENTERED
        elif status == Mark.Status.INVALID:
            raise BusinessRuleError(
                "INVALID is a system status and cannot be set directly.",
                code="VALIDATION_ERROR",
            )
        else:
            mark_status = status

        mark = (
            Mark.objects.select_for_update()
            .filter(exam_candidate=exam_candidate, component=component)
            .first()
        )
        old_value = old_status = None
        action = MarkChangeLog.Action.CREATED
        if mark is None:
            mark = Mark(
                exam_candidate=exam_candidate, component=component,
                school=exam_candidate.school,
            )
        else:
            old_value, old_status = mark.value, mark.status
            action = (
                MarkChangeLog.Action.CLEARED
                if mark_status in NO_VALUE_STATUSES
                else MarkChangeLog.Action.UPDATED
            )
            if old_status != mark_status and old_value == numeric:
                action = MarkChangeLog.Action.STATUS_CHANGE

        mark.value = numeric
        mark.status = mark_status
        mark.entered_by = actor
        if reason:
            mark.remarks = reason
        mark.save()

        if mark.pk and (old_value != numeric or old_status != mark_status):
            MarkChangeLog.objects.create(
                mark=mark,
                exam_candidate=exam_candidate,
                component=component,
                action=MarkChangeLog.Action.CORRECTION if force else action,
                old_value=old_value,
                new_value=numeric,
                old_status=old_status or "",
                new_status=mark_status,
                reason=reason,
                changed_by=actor,
                ip_address=request.META.get("REMOTE_ADDR") if request else None,
            )
        return mark

    @staticmethod
    @transaction.atomic
    def bulk_set(exam_subject: ExamSubject, rows, actor=None, request=None):
        """rows: [{exam_candidate|candidate_number, component|component_code, value?, status?}]
        All-or-nothing: any invalid row aborts the batch."""
        MarksService._check_marks_open(exam_subject)
        components = {str(c.pk): c for c in exam_subject.components.filter(status="ACTIVE")}
        components.update({c.code.lower(): c for c in components.values()})
        enrollments = {
            str(e.pk): e
            for e in ExaminationCandidate.objects.filter(
                examination=exam_subject.examination,
                status__in=ExaminationCandidate.PARTICIPATING,
            )
        }
        by_number = {e.candidate_number: e for e in enrollments.values()}

        marks = []
        errors = []
        for i, row in enumerate(rows):
            try:
                enrollment = enrollments.get(str(row.get("exam_candidate") or "")) or by_number.get(
                    row.get("candidate_number") or ""
                )
                if enrollment is None:
                    raise BusinessRuleError("Candidate not enrolled.", code="NOT_ENROLLED")
                component = components.get(
                    str(row.get("component") or row.get("component_code") or "").lower()
                ) or components.get(str(row.get("component") or ""))
                if component is None:
                    raise BusinessRuleError("Component not found.", code="NOT_FOUND")
                marks.append(
                    MarksService.set_mark(
                        enrollment,
                        component,
                        value=row.get("value"),
                        status=row.get("status"),
                        actor=actor,
                        reason=row.get("reason", "Bulk entry"),
                        request=request,
                    )
                )
            except BusinessRuleError as exc:
                errors.append({"row": i + 1, "candidate": row.get("candidate_number"), "error": exc.message})
        if errors:
            transaction.set_rollback(True)
            return {"success": False, "entered": 0, "errors": errors}
        return {"success": True, "entered": len(marks), "errors": []}

    @staticmethod
    def completion(exam_subject: ExamSubject):
        components = exam_subject.components.filter(status="ACTIVE")
        total_expected = components.count() * ExaminationCandidate.objects.filter(
            examination=exam_subject.examination,
            status__in=ExaminationCandidate.PARTICIPATING,
        ).count()
        entered = (
            Mark.objects.filter(component__in=components)
            .exclude(status=Mark.Status.PENDING)
            .count()
        )
        return {
            "expected": total_expected,
            "entered": entered,
            "percent": round(entered / total_expected * 100, 1) if total_expected else 0,
        }
