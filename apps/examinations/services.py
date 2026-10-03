import logging
from decimal import Decimal

from django.db import transaction

from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError

from .models import ExamComponent, Examination, ExamSubject

logger = logging.getLogger("emas.examinations")


class ExaminationService:
    """Examination workflow management."""

    @staticmethod
    @transaction.atomic
    def transition(examination: Examination, new_status: str, actor=None):
        exam = Examination.objects.select_for_update().get(pk=examination.pk)
        if not exam.can_transition_to(new_status):
            raise BusinessRuleError(
                f"Cannot transition examination from {exam.status} to {new_status}.",
                code="INVALID_STATUS_TRANSITION",
            )
        if new_status == Examination.Status.READY:
            ExaminationService.validate_ready(exam)
        if new_status == Examination.Status.PUBLISHED:
            from django.utils import timezone

            exam.published_at = timezone.now()
        if new_status == Examination.Status.FINALIZED:
            from django.utils import timezone

            exam.finalized_at = timezone.now()
            exam.finalized_by = actor
        exam.status = new_status
        exam.save()
        log_action(
            actor=actor, action="EXAM_STATUS_CHANGE", entity=exam, school=exam.school,
            metadata={"new_status": new_status},
        )
        logger.info("Examination %s transitioned to %s", exam.pk, new_status)
        return exam

    @staticmethod
    def validate_ready(examination: Examination):
        """A READY exam needs at least one subject with components and at
        least one enrolled candidate."""
        exam_subjects = examination.exam_subjects.filter(status="ACTIVE")
        if not exam_subjects.exists():
            raise BusinessRuleError(
                "Examination has no active subjects.", code="EXAM_NOT_READY"
            )
        for es in exam_subjects:
            ExamSubjectService.validate_component_configuration(es)
        if not examination.candidates.filter(
            status__in=("ACTIVE", "COMPLETED")
        ).exists():
            raise BusinessRuleError(
                "Examination has no enrolled candidates.", code="EXAM_NOT_READY"
            )
        return True

    @staticmethod
    def structure(examination: Examination) -> dict:
        """The full examination structure exposed via the API."""
        exam_subjects = (
            examination.exam_subjects.filter(status="ACTIVE")
            .select_related("subject")
            .prefetch_related("components")
        )
        candidates_qs = examination.candidates.filter(
            status__in=("ACTIVE", "COMPLETED")
        ).select_related("school")
        schools = {}
        for c in candidates_qs.only("school_id"):
            schools[c.school_id] = None
        school_qs = examination.participating_schools.all()
        return {
            "id": str(examination.pk),
            "name": examination.name,
            "code": examination.code,
            "status": examination.status,
            "total_candidates": examination.candidates.filter(
                status__in=("ACTIVE", "COMPLETED")
            ).count(),
            "total_schools": examination.participating_schools.count() or 1,
            "organizer": {
                "id": str(examination.school_id),
                "school_name": examination.school.school_name,
                "school_code": examination.school.school_code,
            },
            "participating_schools": [
                {"id": str(s.pk), "school_name": s.school_name, "school_code": s.school_code}
                for s in school_qs
            ],
            "candidate_lists": [
                {"id": str(l.pk), "name": l.name, "school": l.school.school_name}
                for l in examination.candidate_lists.select_related("school")
            ],
            "subjects": [
                {
                    "id": str(es.pk),
                    "subject": es.subject.name,
                    "subject_code": es.subject.code,
                    "maximum_marks": str(es.maximum_marks),
                    "pass_mark": str(es.pass_mark) if es.pass_mark is not None else None,
                    "calculation_method": es.calculation_method,
                    "components": [
                        {
                            "id": str(c.pk),
                            "name": c.name,
                            "code": c.code,
                            "maximum_marks": str(c.maximum_marks),
                            "weight": str(c.weight) if c.weight is not None else None,
                            "required": c.required,
                        }
                        for c in es.components.filter(status="ACTIVE")
                    ],
                }
                for es in exam_subjects
            ],
        }


class ExamSubjectService:
    """Configuration of subjects within an examination."""

    @staticmethod
    def active_components(exam_subject: ExamSubject):
        qs = exam_subject.components.filter(status="ACTIVE")
        if not qs.exists():
            ExamSubjectService.ensure_default_component(exam_subject)
            qs = exam_subject.components.filter(status="ACTIVE")
        return qs

    @staticmethod
    def ensure_default_component(exam_subject: ExamSubject) -> ExamComponent:
        """Guarantee a single default component mirroring the subject max so
        every subject has at least one markable unit."""
        component, created = ExamComponent.objects.get_or_create(
            exam_subject=exam_subject,
            code="FINAL",
            defaults={
                "name": exam_subject.subject.name,
                "maximum_marks": exam_subject.maximum_marks,
            },
        )
        if not created and exam_subject.components.filter(status="ACTIVE").count() == 1:
            if component.maximum_marks != exam_subject.maximum_marks:
                component.maximum_marks = exam_subject.maximum_marks
                component.save(update_fields=["maximum_marks"])
        return component

    @staticmethod
    @transaction.atomic
    def validate_component_configuration(exam_subject: ExamSubject):
        components = list(exam_subject.components.filter(status="ACTIVE"))
        if not components:
            return True
        if exam_subject.calculation_method == ExamSubject.CalculationMethod.WEIGHTED:
            total = sum(c.weight or Decimal("0") for c in components)
            if total != Decimal("100"):
                raise BusinessRuleError(
                    f"Weighted components for {exam_subject.subject.name} must sum to 100%, "
                    f"got {total}%.",
                    code="INVALID_WEIGHTS",
                )
            for c in components:
                if c.weight is None:
                    raise BusinessRuleError(
                        f"Component {c.name} is missing a weight.", code="INVALID_WEIGHTS"
                    )
        return True
