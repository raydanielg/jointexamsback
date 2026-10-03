import json
import logging
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
import uuid

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError
from apps.enrollment.models import ExaminationCandidate
from apps.examinations.models import Examination, ExamSubject
from apps.examinations.services import ExamSubjectService
from apps.grading.services import GradeCalculationService
from apps.marks.models import Mark, MarkChangeLog
from apps.ranking.services import RankingService

from .models import (
    CandidateExamResult,
    CandidateSubjectResult,
    ResultCorrectionRequest,
    ResultSnapshot,
    ResultStatus,
)

logger = logging.getLogger("emas.results")


class ResultCalculationService:
    """Centralized result engine — the only place marks become results."""

    @staticmethod
    def _aggregate_subject(exam_subject, marks):
        """marks: list of Mark rows for one candidate+subject. Returns
        (score, max_score, percentage, status, breakdown)."""
        components = list(ExamSubjectService.active_components(exam_subject))
        breakdown = {}
        values = {}  # component_id -> Decimal or None
        statuses = {}
        by_component = {m.component_id: m for m in marks}
        for comp in components:
            mark = by_component.get(comp.pk)
            if mark is None:
                statuses[comp.pk] = Mark.Status.PENDING if comp.required else None
                values[comp.pk] = None
                breakdown[comp.code] = {
                    "value": None,
                    "status": Mark.Status.PENDING,
                    "max": str(comp.maximum_marks),
                }
            else:
                statuses[comp.pk] = mark.status
                values[comp.pk] = mark.value
                breakdown[comp.code] = {
                    "value": str(mark.value) if mark.value is not None else None,
                    "status": mark.status,
                    "max": str(comp.maximum_marks),
                }

        required_statuses = [statuses[c.pk] for c in components if c.required]
        if required_statuses and all(s == Mark.Status.ABSENT for s in required_statuses):
            return None, None, None, ResultStatus.ABSENT, breakdown
        if required_statuses and all(s == Mark.Status.EXEMPT for s in required_statuses):
            return None, None, None, ResultStatus.EXEMPT, breakdown
        if any(s in (Mark.Status.PENDING, Mark.Status.MISSING) for s in required_statuses):
            return None, None, None, ResultStatus.INCOMPLETE, breakdown
        if any(s == Mark.Status.INVALID for s in required_statuses):
            return None, None, None, ResultStatus.INCOMPLETE, breakdown
        if any(s == Mark.Status.ABSENT for s in required_statuses):
            return None, None, None, ResultStatus.ABSENT, breakdown

        method = exam_subject.calculation_method
        if method == ExamSubject.CalculationMethod.WEIGHTED:
            weighted = Decimal("0")
            for comp in components:
                v = values[comp.pk]
                if v is None:
                    continue
                pct = v / comp.maximum_marks * Decimal("100")
                weighted += pct * (comp.weight or Decimal("0")) / Decimal("100")
            score = weighted * exam_subject.maximum_marks / Decimal("100")
            percentage = weighted
            return score, exam_subject.maximum_marks, percentage, ResultStatus.SCORED, breakdown

        raw = sum(v for v in values.values() if v is not None)
        max_raw = sum(c.maximum_marks for c in components)
        if method == ExamSubject.CalculationMethod.RAW_TOTAL:
            score = raw
            percentage = (raw / max_raw * Decimal("100")) if max_raw else None
            return score, max_raw, percentage, ResultStatus.SCORED, breakdown
        # NORMALIZED: scale raw/total onto exam_subject.maximum_marks
        if not max_raw:
            return None, None, None, ResultStatus.INCOMPLETE, breakdown
        percentage = raw / max_raw * Decimal("100")
        score = percentage * exam_subject.maximum_marks / Decimal("100")
        return score, exam_subject.maximum_marks, percentage, ResultStatus.SCORED, breakdown

    @staticmethod
    def _scheme(examination):
        scheme = examination.grading_scheme
        if scheme is None:
            from apps.grading.models import GradingScheme

            scheme = GradingScheme.objects.filter(
                school=examination.school, is_default=True
            ).first()
        return scheme

    @staticmethod
    @transaction.atomic
    def calculate_exam(examination: Examination, actor=None):
        """Idempotent full calculation: subject rows + overall rows +
        positions. Safe to re-run; updates in place."""
        exam = (
            Examination.objects.select_for_update(of=("self",))
            .select_related("grading_scheme")
            .get(pk=examination.pk)
        )
        if exam.status in (Examination.Status.PUBLISHED, Examination.Status.ARCHIVED):
            raise BusinessRuleError(
                "Results on a published or archived examination cannot be recalculated "
                "outside the correction workflow.",
                code="EXAM_LOCKED",
            )

        scheme = ResultCalculationService._scheme(exam)
        exam_subjects = list(
            exam.exam_subjects.filter(status="ACTIVE")
            .select_related("subject")
            .prefetch_related("components")
        )
        enrollments = list(
            ExaminationCandidate.objects.filter(
                examination=exam, status__in=ExaminationCandidate.PARTICIPATING
            ).select_related("candidate", "school")
        )
        if not enrollments:
            return {"candidates": 0, "subjects": 0}

        all_marks = Mark.objects.filter(
            exam_candidate__in=enrollments,
            component__exam_subject__in=exam_subjects,
        ).select_related("component")
        marks_map = {}
        for m in all_marks:
            marks_map.setdefault((m.exam_candidate_id, m.component.exam_subject_id), []).append(m)

        existing_subject_results = {
            (r.exam_candidate_id, r.exam_subject_id): r
            for r in CandidateSubjectResult.objects.filter(exam_candidate__in=enrollments)
        }
        existing_exam_results = {
            r.exam_candidate_id: r
            for r in CandidateExamResult.objects.filter(exam_candidate__in=enrollments)
        }

        subject_rows = []
        subject_by_exam_subject = {}
        for enrollment in enrollments:
            for exam_subject in exam_subjects:
                marks = marks_map.get((enrollment.pk, exam_subject.pk), [])
                score, max_score, pct, status, breakdown = (
                    ResultCalculationService._aggregate_subject(exam_subject, marks)
                )
                grade, points, is_pass, remark = GradeCalculationService.grade_for(pct, scheme)
                result = existing_subject_results.get((enrollment.pk, exam_subject.pk))
                if result is None:
                    result = CandidateSubjectResult(
                        school=enrollment.school,
                        examination=exam,
                        exam_candidate=enrollment,
                        exam_subject=exam_subject,
                    )
                    result._is_new = True
                result.score = score
                result.max_score = max_score
                result.percentage = pct
                result.grade = grade or ""
                result.points = points
                result.is_pass = bool(is_pass)
                result.remark = remark or ""
                result.status = status
                result.component_breakdown = breakdown
                result.calc_version = (result.calc_version or 0) + (0 if getattr(result, "_is_new", False) else 1)
                subject_rows.append(result)
                subject_by_exam_subject.setdefault(exam_subject.pk, []).append(result)

        for exam_subject in exam_subjects:
            rows = subject_by_exam_subject.get(exam_subject.pk, [])
            RankingService.assign_subject_positions(exam, exam_subject, rows)

        CandidateSubjectResult.objects.bulk_create(
            [r for r in subject_rows if getattr(r, "_is_new", False)]
        )
        CandidateSubjectResult.objects.bulk_update(
            [r for r in subject_rows if not getattr(r, "_is_new", False)],
            fields=[
                "score", "max_score", "percentage", "grade", "points", "is_pass",
                "status", "position", "school_position", "component_breakdown",
                "calc_version", "computed_at",
            ],
        )

        # Overall per-candidate results
        exam_rows = []
        for enrollment in enrollments:
            per_subject = [
                r for r in subject_rows if r.exam_candidate_id == enrollment.pk
            ]
            result = existing_exam_results.get(enrollment.pk)
            if result is None:
                result = CandidateExamResult(
                    school=enrollment.school, examination=exam, exam_candidate=enrollment
                )
                result._is_new = True
            scored = [r for r in per_subject if r.status == ResultStatus.SCORED]
            incomplete = any(r.status == ResultStatus.INCOMPLETE for r in per_subject)
            absent = per_subject and all(r.status == ResultStatus.ABSENT for r in per_subject)

            result.subject_count = len(per_subject)
            result.scored_subjects = len(scored)
            if absent:
                result.status = ResultStatus.ABSENT
                result.total_score = result.total_max = result.average_percentage = None
                result.grade = result.division = ""
                result.points_sum = None
                result.position = result.school_position = result.list_position = None
            elif incomplete or not scored:
                result.status = ResultStatus.INCOMPLETE
                result.total_score = result.total_max = result.average_percentage = None
                result.grade = result.division = ""
                result.points_sum = None
                result.position = result.school_position = result.list_position = None
            else:
                result.status = ResultStatus.SCORED
                weights = {es.pk: es.weight or Decimal("1") for es in exam_subjects}
                total_w = sum(weights[r.exam_subject_id] for r in scored)
                weighted_pct = sum(
                    (r.percentage or Decimal("0")) * weights[r.exam_subject_id] for r in scored
                ) / total_w
                result.total_score = sum(r.score or Decimal("0") for r in scored)
                result.total_max = sum(r.max_score or Decimal("0") for r in scored)
                result.average_percentage = weighted_pct.quantize(Decimal("0.01"))
                grade, _, _, _ = GradeCalculationService.grade_for(result.average_percentage, scheme)
                result.grade = grade or ""
                # Division = sum of grade points of best-N scored subjects
                n = exam.division_best_subjects or len(scored)
                best = sorted(
                    (r.points for r in scored if r.points is not None)
                )[:n]
                result.points_sum = sum(best) if best else None
                if result.points_sum is not None and len(scored) >= n:
                    result.division = GradeCalculationService.division_for(result.points_sum, scheme) or ""
                else:
                    result.division = ""
                result.calc_version = (result.calc_version or 0) + (
                    0 if getattr(result, "_is_new", False) else 1
                )
            exam_rows.append(result)

        RankingService.assign_exam_positions(exam, exam_rows)

        CandidateExamResult.objects.bulk_create(
            [r for r in exam_rows if getattr(r, "_is_new", False)]
        )
        CandidateExamResult.objects.bulk_update(
            [r for r in exam_rows if not getattr(r, "_is_new", False)],
            fields=[
                "total_score", "total_max", "average_percentage", "grade", "points_sum",
                "division", "position", "school_position", "list_position", "status",
                "subject_count", "scored_subjects", "calc_version", "computed_at",
            ],
        )

        for enrollment in enrollments:
            if enrollment.status == ExaminationCandidate.Status.ACTIVE:
                res = existing_exam_results.get(enrollment.pk) or next(
                    (r for r in exam_rows if r.exam_candidate_id == enrollment.pk), None
                )
                if res and res.status == ResultStatus.SCORED:
                    enrollment.status = ExaminationCandidate.Status.COMPLETED
        ExaminationCandidate.objects.bulk_update(enrollments, fields=["status"])

        log_action(
            actor=actor, action="RESULTS_CALCULATED", entity=exam, school=exam.school,
            metadata={"candidates": len(enrollments), "subjects": len(exam_subjects)},
        )
        logger.info(
            "Calculated %d candidates x %d subjects for exam %s",
            len(enrollments), len(exam_subjects), exam.pk,
        )
        return {"candidates": len(enrollments), "subjects": len(exam_subjects)}

    @staticmethod
    def calculate_for_candidate(exam_candidate: ExaminationCandidate, actor=None):
        """Targeted recalculation used by the correction workflow. Bypasses
        the published lock because corrections may need it post-publish."""
        examination = Examination.objects.get(pk=exam_candidate.examination_id)
        return ResultCalculationService._calculate_for_published(
            examination, enrollments=[exam_candidate]
        )

    @staticmethod
    @transaction.atomic
    def _calculate_for_published(examination, enrollments=None):
        """Same engine as calculate_exam but permitted post-publish and
        scoped to specific candidates. Re-ranks the whole exam afterwards
        so positions stay consistent."""
        exam = Examination.objects.select_for_update().get(pk=examination.pk)
        scheme = ResultCalculationService._scheme(exam)
        exam_subjects = list(
            exam.exam_subjects.filter(status="ACTIVE").prefetch_related("components")
        )
        targets = enrollments or list(
            ExaminationCandidate.objects.filter(
                examination=exam,
                status__in=(ExaminationCandidate.Status.ACTIVE, ExaminationCandidate.Status.COMPLETED),
            )
        )
        marks = Mark.objects.filter(
            exam_candidate__in=targets, component__exam_subject__in=exam_subjects
        ).select_related("component")
        marks_map = {}
        for m in marks:
            marks_map.setdefault((m.exam_candidate_id, m.component.exam_subject_id), []).append(m)

        changed_enrollment_ids = [e.pk for e in targets]
        for enrollment in targets:
            for exam_subject in exam_subjects:
                subj_marks = marks_map.get((enrollment.pk, exam_subject.pk), [])
                score, max_score, pct, status, breakdown = (
                    ResultCalculationService._aggregate_subject(exam_subject, subj_marks)
                )
                grade, points, is_pass, remark = GradeCalculationService.grade_for(pct, scheme)
                CandidateSubjectResult.objects.update_or_create(
                    exam_candidate=enrollment, exam_subject=exam_subject,
                    defaults={
                        "school": enrollment.school, "examination": exam,
                        "score": score, "max_score": max_score, "percentage": pct,
                        "grade": grade or "",
                        "points": points,
                        "is_pass": bool(is_pass),
                        "status": status, "component_breakdown": breakdown,
                    },
                )

        # Refresh overall rows for affected candidates, then re-rank the
        # whole exam so positions remain consistent.
        for enrollment in targets:
            per_subject = list(
                CandidateSubjectResult.objects.filter(
                    exam_candidate=enrollment, exam_subject__in=exam_subjects
                )
            )
            result, _ = CandidateExamResult.objects.get_or_create(
                exam_candidate=enrollment,
                defaults={"school": enrollment.school, "examination": exam},
            )
            scored = [r for r in per_subject if r.status == ResultStatus.SCORED]
            incomplete = any(r.status == ResultStatus.INCOMPLETE for r in per_subject)
            absent = per_subject and all(r.status == ResultStatus.ABSENT for r in per_subject)
            result.subject_count = len(per_subject)
            result.scored_subjects = len(scored)
            if absent or incomplete or not scored:
                result.status = (
                    ResultStatus.ABSENT if absent else ResultStatus.INCOMPLETE
                )
                result.total_score = result.total_max = result.average_percentage = None
                result.grade = result.division = ""
                result.points_sum = None
            else:
                result.status = ResultStatus.SCORED
                weights = {es.pk: es.weight or Decimal("1") for es in exam_subjects}
                total_w = sum(weights[r.exam_subject_id] for r in scored)
                result.average_percentage = (
                    sum((r.percentage or 0) * weights[r.exam_subject_id] for r in scored) / total_w
                ).quantize(Decimal("0.01"))
                result.total_score = sum(r.score or 0 for r in scored)
                result.total_max = sum(r.max_score or 0 for r in scored)
                grade, _, _, _ = GradeCalculationService.grade_for(result.average_percentage, scheme)
                result.grade = grade or ""
                n = exam.division_best_subjects or len(scored)
                best = sorted(r.points for r in scored if r.points is not None)[:n]
                result.points_sum = sum(best) if best else None
                if result.points_sum is not None and len(scored) >= n:
                    result.division = GradeCalculationService.division_for(result.points_sum, scheme) or ""
                else:
                    result.division = ""
            result.calc_version = (result.calc_version or 0) + 1
            result.save()

        all_results = list(
            CandidateExamResult.objects.filter(examination=exam).select_related("exam_candidate")
        )
        RankingService.assign_exam_positions(exam, all_results)
        CandidateExamResult.objects.bulk_update(
            all_results, fields=["position", "school_position", "list_position"]
        )
        all_subject_results = list(
            CandidateSubjectResult.objects.filter(examination=exam).select_related("exam_candidate")
        )
        by_es = {}
        for r in all_subject_results:
            by_es.setdefault(r.exam_subject_id, []).append(r)
        for es_id, rows in by_es.items():
            RankingService.assign_subject_positions(exam, rows[0].exam_subject, rows)
        CandidateSubjectResult.objects.bulk_update(
            all_subject_results, fields=["position", "school_position"]
        )
        return {"recalculated": len(changed_enrollment_ids)}


class ResultSnapshotService:
    @staticmethod
    def create_snapshot(examination: Examination, kind, actor=None) -> ResultSnapshot:
        version = (
            ResultSnapshot.objects.filter(examination=examination, kind=kind)
            .count() + 1
        )
        exam_results = list(
            CandidateExamResult.objects.filter(examination=examination)
            .select_related("exam_candidate__candidate", "exam_candidate__school")
        )
        subject_results = list(
            CandidateSubjectResult.objects.filter(examination=examination)
            .select_related("exam_subject__subject", "exam_candidate")
        )
        scheme = examination.grading_scheme
        scheme_snapshot = None
        if scheme:
            scheme_snapshot = {
                "id": str(scheme.pk),
                "name": scheme.name,
                "bands": [
                    {
                        "grade": b.grade,
                        "min": str(b.min_percentage),
                        "max": str(b.max_percentage),
                        "points": str(b.points),
                        "is_pass": b.is_pass,
                    }
                    for b in scheme.bands.all()
                ],
                "divisions": [
                    {"name": d.name, "min": d.min_points, "max": d.max_points}
                    for d in scheme.division_bands.all()
                ],
            }
        ranking_snapshot = None
        if examination.ranking_config:
            rc = examination.ranking_config
            ranking_snapshot = {
                "id": str(rc.pk), "name": rc.name, "method": rc.method, "rank_by": rc.rank_by,
            }
        payload_rows = []
        subject_index = {}
        for r in subject_results:
            subject_index.setdefault(str(r.exam_candidate_id), []).append(
                {
                    "subject": r.exam_subject.subject.name,
                    "subject_code": r.exam_subject.subject.code,
                    "score": str(r.score) if r.score is not None else None,
                    "percentage": str(r.percentage) if r.percentage is not None else None,
                    "grade": r.grade,
                    "points": str(r.points) if r.points is not None else None,
                    "position": r.position,
                    "status": r.status,
                    "breakdown": r.component_breakdown,
                }
            )
        for r in exam_results:
            ec = r.exam_candidate
            payload_rows.append(
                {
                    "exam_candidate_id": str(ec.pk),
                    "candidate_number": ec.candidate_number,
                    "candidate_name": ec.candidate.full_name,
                    "school_id": str(ec.school_id),
                    "school_name": ec.school.school_name,
                    "total_score": str(r.total_score) if r.total_score is not None else None,
                    "average_percentage": str(r.average_percentage) if r.average_percentage is not None else None,
                    "grade": r.grade,
                    "division": r.division,
                    "position": r.position,
                    "school_position": r.school_position,
                    "status": r.status,
                    "subjects": subject_index.get(str(ec.pk), []),
                }
            )
        payload_rows.sort(key=lambda x: (x["position"] is None, x["position"] or 0))
        payload = {
            "examination": {"name": examination.name, "code": examination.code},
            "generated_at": timezone.now().isoformat(),
            "candidates": payload_rows,
        }
        # Normalize non-JSON primitives (Decimal, UUID, datetime).
        def _json_safe(value):
            return json.loads(json.dumps(value, cls=DjangoJSONEncoder))

        return ResultSnapshot.objects.create(
            examination=examination,
            kind=kind,
            version=version,
            grading_scheme_snapshot=_json_safe(scheme_snapshot or {}),
            ranking_config_snapshot=_json_safe(ranking_snapshot or {}),
            payload=_json_safe(payload),
            created_by=actor,
        )


class ResultPublicationService:
    @staticmethod
    @transaction.atomic
    def finalize(examination: Examination, actor=None):
        """MARKS_ENTRY/UNDER_REVIEW -> FINALIZED (auto-calculates first)."""
        exam = Examination.objects.select_for_update().get(pk=examination.pk)
        if exam.status == Examination.Status.MARKS_ENTRY:
            ResultCalculationService.calculate_exam(exam, actor=actor)
            from apps.examinations.services import ExaminationService

            exam = ExaminationService.transition(
                exam, Examination.Status.UNDER_REVIEW, actor=actor
            )
        if exam.status != Examination.Status.UNDER_REVIEW:
            raise BusinessRuleError(
                "Only examinations under review can be finalized.", code="INVALID_STATUS_TRANSITION"
            )
        ResultCalculationService.calculate_exam(exam, actor=actor)
        from apps.examinations.services import ExaminationService

        exam = ExaminationService.transition(exam, Examination.Status.FINALIZED, actor=actor)
        ResultSnapshotService.create_snapshot(exam, ResultSnapshot.Kind.FINALIZED, actor)
        log_action(actor=actor, action="RESULTS_FINALIZED", entity=exam, school=exam.school)
        return exam

    @staticmethod
    @transaction.atomic
    def publish(examination: Examination, actor=None):
        exam = Examination.objects.select_for_update().get(pk=examination.pk)
        if exam.status == Examination.Status.UNDER_REVIEW:
            exam = ResultPublicationService.finalize(exam, actor=actor)
        if exam.status != Examination.Status.FINALIZED:
            raise BusinessRuleError(
                "Only finalized examinations can be published.", code="INVALID_STATUS_TRANSITION"
            )
        from apps.examinations.services import ExaminationService

        exam = ExaminationService.transition(exam, Examination.Status.PUBLISHED, actor=actor)
        if not exam.public_token:
            exam.public_token = uuid.uuid4()
            exam.save(update_fields=["public_token", "updated_at"])
        snapshot = ResultSnapshotService.create_snapshot(exam, ResultSnapshot.Kind.PUBLISHED, actor)
        log_action(actor=actor, action="RESULTS_PUBLISHED", entity=exam, school=exam.school)
        return snapshot


class ResultCorrectionService:
    """Corrections to published results — request, review, apply."""

    @staticmethod
    def create_request(examination, exam_candidate, component, requested_value=None,
                       requested_status="", reason="", actor=None):
        examination = Examination.objects.get(pk=examination.pk)
        if examination.status != Examination.Status.PUBLISHED:
            raise BusinessRuleError(
                "Correction requests apply to published examinations only. "
                "Draft examinations can be edited directly.",
                code="NOT_PUBLISHED",
            )
        if not reason:
            raise BusinessRuleError("A reason is required.", code="VALIDATION_ERROR")
        mark = Mark.objects.filter(
            exam_candidate=exam_candidate, component=component
        ).first()
        return ResultCorrectionRequest.objects.create(
            examination=examination,
            exam_candidate=exam_candidate,
            component=component,
            old_value=mark.value if mark else None,
            old_status=mark.status if mark else "",
            requested_value=requested_value,
            requested_status=requested_status,
            reason=reason,
            requested_by=actor,
        )

    @staticmethod
    @transaction.atomic
    def review(correction: ResultCorrectionRequest, actor, approve: bool, note=""):
        correction = ResultCorrectionRequest.objects.select_for_update().get(pk=correction.pk)
        if correction.status != ResultCorrectionRequest.Status.PENDING:
            raise BusinessRuleError("Only pending corrections can be reviewed.", code="INVALID_STATUS")
        correction.status = (
            ResultCorrectionRequest.Status.APPROVED if approve else ResultCorrectionRequest.Status.REJECTED
        )
        correction.reviewed_by = actor
        correction.review_note = note
        correction.reviewed_at = timezone.now()
        correction.save()
        log_action(
            actor=actor,
            action="CORRECTION_APPROVED" if approve else "CORRECTION_REJECTED",
            entity=correction, school=correction.examination.school,
        )
        return correction

    @staticmethod
    @transaction.atomic
    def apply(correction: ResultCorrectionRequest, actor=None):
        correction = ResultCorrectionRequest.objects.select_for_update().get(pk=correction.pk)
        if correction.status != ResultCorrectionRequest.Status.APPROVED:
            raise BusinessRuleError("Only approved corrections can be applied.", code="INVALID_STATUS")

        from apps.marks.services import MarksService

        MarksService.set_mark(
            correction.exam_candidate,
            correction.component,
            value=correction.requested_value,
            status=correction.requested_status or None,
            actor=actor,
            reason=f"Correction {correction.pk}: {correction.reason}",
            force=True,
        )
        ResultCalculationService.calculate_for_candidate(
            correction.exam_candidate, actor=actor
        )
        correction.status = ResultCorrectionRequest.Status.APPLIED
        correction.applied_at = timezone.now()
        correction.save(update_fields=["status", "applied_at", "updated_at"])
        ResultSnapshotService.create_snapshot(
            correction.examination, ResultSnapshot.Kind.CORRECTED, actor
        )
        log_action(
            actor=actor, action="CORRECTION_APPLIED", entity=correction,
            school=correction.examination.school,
            metadata={"old": str(correction.old_value), "new": str(correction.requested_value)},
        )
        return correction
