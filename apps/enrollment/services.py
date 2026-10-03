import logging

from django.db import transaction
from django.db.models import Count

from apps.audit.services import log_action
from apps.candidate_lists.models import CandidateList
from apps.core.exceptions import BusinessRuleError
from apps.examinations.models import Examination

from .models import ExaminationCandidate

logger = logging.getLogger("emas.enrollment")


class ExamEnrollmentService:
    """Idempotent list-based enrollment of candidates into examinations."""

    @staticmethod
    def _next_exam_number(exam, school, counters):
        """Per-school sequential examination candidate number, e.g.
        ``MWZ/2026/0007`` — continues after the highest number already
        assigned to that school in this exam."""
        year = exam.start_date.year if exam.start_date else exam.created_at.year
        prefix = f"{school.school_code}/{year}/"
        key = school.pk
        if key not in counters:
            last = (
                exam.candidates.filter(
                    school_id=school.pk, candidate_number__startswith=prefix
                )
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
            counters[key] = seq
        counters[key] += 1
        return f"{prefix}{counters[key]:04d}"

    @staticmethod
    def preview(examination: Examination, list_ids=None, candidate_ids=None):
        lists = CandidateList.objects.filter(pk__in=list_ids or [])
        candidate_id_set = set()
        for lst in lists:
            candidate_id_set.update(lst.candidates.values_list("pk", flat=True))
        candidate_id_set.update(candidate_ids or [])
        already = set(
            ExaminationCandidate.objects.filter(
                examination=examination, candidate_id__in=candidate_id_set
            ).values_list("candidate_id", flat=True)
        )
        return {
            "lists": [{"id": str(l.pk), "name": l.name, "candidates": l.entries.count()} for l in lists],
            "unique_candidates": len(candidate_id_set),
            "already_enrolled": len(already),
            "would_create": len(candidate_id_set - already),
        }

    @staticmethod
    @transaction.atomic
    def enroll_lists(examination: Examination, list_ids, actor=None):
        exam = Examination.objects.select_for_update().get(pk=examination.pk)
        if exam.status in (
            Examination.Status.FINALIZED,
            Examination.Status.PUBLISHED,
            Examination.Status.ARCHIVED,
        ):
            raise BusinessRuleError(
                "Cannot enroll candidates in a finalized or published examination.",
                code="EXAM_LOCKED",
            )

        lists = list(CandidateList.objects.filter(pk__in=list_ids).select_related("school"))
        if not lists:
            raise BusinessRuleError("No candidate lists selected.", code="VALIDATION_ERROR")

        # Lists may come from any organization the actor administers. Their
        # schools are auto-added as participants — joint examinations pull
        # candidates from several organizations.
        allowed_school_ids = {exam.school_id} | set(
            exam.participating_schools.values_list("pk", flat=True)
        )
        actor_school_ids = set()
        if actor is not None and not actor.is_superadmin:
            actor_school_ids = set(
                actor.memberships.filter(is_active=True).values_list("school_id", flat=True)
            )
        for lst in lists:
            if lst.school_id in allowed_school_ids:
                continue
            if (actor is not None and actor.is_superadmin) or (
                actor_school_ids and lst.school_id in actor_school_ids
            ):
                exam.participating_schools.add(lst.school)
                allowed_school_ids.add(lst.school_id)
                continue
            raise BusinessRuleError(
                f"List '{lst.name}' belongs to a school not participating in this examination.",
                code="PERMISSION_DENIED",
            )

        # De-duplicate candidates across all selected lists while keeping
        # first-list attribution.
        candidate_map = {}  # candidate_id -> list
        for lst in lists:
            for entry in lst.entries.select_related("candidate").iterator():
                if entry.candidate_id not in candidate_map:
                    candidate_map[entry.candidate_id] = lst

        already = set(
            ExaminationCandidate.objects.filter(
                examination=exam, candidate_id__in=candidate_map.keys()
            ).values_list("candidate_id", flat=True)
        )

        from apps.candidates.models import Candidate

        candidates = {
            c.pk: c
            for c in Candidate.objects.filter(
                pk__in=candidate_map.keys()
            ).select_related("school")
        }
        counters = {}
        to_create = [
            ExaminationCandidate(
                examination=exam,
                candidate_id=cid,
                school_id=candidates[cid].school_id,
                candidate_number=(
                    candidates[cid].candidate_number
                    or ExamEnrollmentService._next_exam_number(
                        exam, candidates[cid].school, counters
                    )
                ),
                status=ExaminationCandidate.Status.ACTIVE,
                source_list=lst,
                enrolled_by=actor,
            )
            for cid, lst in candidate_map.items()
            if cid not in already
        ]
        created = ExaminationCandidate.objects.bulk_create(to_create, ignore_conflicts=True)
        exam.candidate_lists.add(*lists)

        log_action(
            actor=actor, action="EXAM_ENROLL", entity=exam, school=exam.school,
            metadata={
                "lists": [str(l.pk) for l in lists],
                "created": len(to_create),
                "skipped_existing": len(already),
            },
        )
        logger.info(
            "Enrolled %d candidates into exam %s from %d lists",
            len(to_create), exam.pk, len(lists),
        )
        return {
            "created": len(to_create),
            "already_enrolled": len(already),
            "total": exam.candidates.count(),
        }

    @staticmethod
    @transaction.atomic
    def enroll_candidates(examination: Examination, candidate_ids, actor=None):
        exam = Examination.objects.select_for_update().get(pk=examination.pk)
        if exam.status in (
            Examination.Status.FINALIZED,
            Examination.Status.PUBLISHED,
            Examination.Status.ARCHIVED,
        ):
            raise BusinessRuleError("Examination is locked.", code="EXAM_LOCKED")
        already = set(
            ExaminationCandidate.objects.filter(
                examination=exam, candidate_id__in=candidate_ids
            ).values_list("candidate_id", flat=True)
        )
        from apps.candidates.models import Candidate

        candidates = Candidate.objects.filter(pk__in=candidate_ids).select_related("school")
        allowed_school_ids = {exam.school_id} | set(
            exam.participating_schools.values_list("pk", flat=True)
        )
        actor_school_ids = set()
        if actor is not None and not actor.is_superadmin:
            actor_school_ids = set(
                actor.memberships.filter(is_active=True).values_list("school_id", flat=True)
            )
        counters = {}
        to_create = []
        for c in candidates:
            if c.pk in already:
                continue
            if c.school_id not in allowed_school_ids:
                # Auto-add the candidate's school as a participant when the
                # actor administers it — organizations enroll jointly.
                own = actor is not None and actor.is_superadmin
                if own or c.school_id in actor_school_ids:
                    exam.participating_schools.add(c.school)
                    allowed_school_ids.add(c.school_id)
                else:
                    raise BusinessRuleError(
                        f"Candidate {c.candidate_number} belongs to a non-participating school.",
                        code="PERMISSION_DENIED",
                    )
            to_create.append(
                ExaminationCandidate(
                    examination=exam, candidate=c, school=c.school,
                    candidate_number=(
                        c.candidate_number
                        or ExamEnrollmentService._next_exam_number(
                            exam, c.school, counters
                        )
                    ),
                    enrolled_by=actor,
                )
            )
        ExaminationCandidate.objects.bulk_create(to_create, ignore_conflicts=True)
        log_action(
            actor=actor, action="EXAM_ENROLL", entity=exam, school=exam.school,
            metadata={"created": len(to_create), "direct": True},
        )
        return {"created": len(to_create), "already_enrolled": len(already)}

    @staticmethod
    @transaction.atomic
    def withdraw(exam_candidate: ExaminationCandidate, actor=None):
        exam_candidate.status = ExaminationCandidate.Status.WITHDRAWN
        exam_candidate.save(update_fields=["status", "updated_at"])
        log_action(
            actor=actor, action="CANDIDATE_WITHDRAWN", entity=exam_candidate,
            school=exam_candidate.school,
        )
        return exam_candidate

    @staticmethod
    @transaction.atomic
    def remove(exam_candidate: ExaminationCandidate, actor=None):
        exam = exam_candidate.examination
        if exam.status != Examination.Status.DRAFT:
            raise BusinessRuleError(
                "Candidates can only be removed while the examination is a draft; "
                "withdraw them instead.",
                code="EXAM_LOCKED",
            )
        log_action(
            actor=actor, action="CANDIDATE_REMOVED", entity=exam_candidate,
            school=exam_candidate.school,
        )
        exam_candidate.delete()
