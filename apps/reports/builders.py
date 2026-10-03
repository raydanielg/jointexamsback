"""Report data builders — read the same calculated result rows the API
exposes; nothing here re-implements calculation logic."""
from collections import defaultdict

from django.db.models import Avg, Count, Max, Min, Q

from apps.enrollment.models import ExaminationCandidate
from apps.examinations.models import ExamSubject
from apps.marks.models import Mark
from apps.results.models import (
    CandidateExamResult,
    CandidateSubjectResult,
    ResultSnapshot,
    ResultStatus,
)


def _exam_results(exam):
    return list(
        CandidateExamResult.objects.filter(examination=exam)
        .select_related("exam_candidate__candidate", "exam_candidate__school")
        .order_by("position")
    )


def _subject_results(exam):
    return list(
        CandidateSubjectResult.objects.filter(examination=exam)
        .select_related(
            "exam_candidate__candidate", "exam_candidate__school", "exam_subject__subject"
        )
        .order_by("exam_subject__display_order", "position")
    )


def build_report_data(exam, report_type, params=None):
    params = params or {}
    builder = _BUILDERS.get(report_type)
    if builder is None:
        from apps.core.exceptions import BusinessRuleError

        raise BusinessRuleError(f"Unknown report type '{report_type}'.", code="VALIDATION_ERROR")
    return builder(exam, params)


def _candidate_result(exam, params):
    results = _exam_results(exam)
    subject_rows = _subject_results(exam)
    by_candidate = defaultdict(list)
    for r in subject_rows:
        by_candidate[r.exam_candidate_id].append(r)
    candidate_id = params.get("exam_candidate")
    if candidate_id:
        results = [r for r in results if str(r.exam_candidate_id) == str(candidate_id)]
    if params.get("school"):
        results = [r for r in results if str(r.exam_candidate.school_id) == params["school"]]
    return {
        "title": "Candidate Results",
        "exam": exam,
        "candidates": [
            {"result": r, "subjects": by_candidate.get(r.exam_candidate_id, [])}
            for r in results
        ],
    }


def _result_slip(exam, params):
    candidate_id = params.get("exam_candidate")
    result = (
        CandidateExamResult.objects.filter(
            examination=exam, exam_candidate_id=candidate_id
        )
        .select_related("exam_candidate__candidate", "exam_candidate__school")
        .first()
    )
    if result is None:
        from apps.core.exceptions import BusinessRuleError

        raise BusinessRuleError("Result not found.", code="NOT_FOUND")
    subjects = CandidateSubjectResult.objects.filter(
        exam_candidate=result.exam_candidate, examination=exam
    ).select_related("exam_subject__subject").order_by("exam_subject__display_order")
    return {
        "title": "Result Slip",
        "exam": exam,
        "result": result,
        "subjects": list(subjects),
        "school": result.exam_candidate.school,
    }


def _full_results(exam, params):
    results = _exam_results(exam)
    subject_rows = _subject_results(exam)
    subjects = list(exam.exam_subjects.filter(status="ACTIVE").select_related("subject"))
    by_pair = {(r.exam_candidate_id, r.exam_subject_id): r for r in subject_rows}
    return {
        "title": "Full Examination Results (Broadsheet)",
        "exam": exam,
        "subjects": subjects,
        "rows": [
            {
                "result": r,
                "subject_cells": [by_pair.get((r.exam_candidate_id, es.pk)) for es in subjects],
            }
            for r in results
        ],
    }


def _subject_results_report(exam, params):
    subject_id = params.get("exam_subject")
    qs = _subject_results(exam)
    if subject_id:
        qs = [r for r in qs if str(r.exam_subject_id) == str(subject_id)]
    grouped = defaultdict(list)
    for r in qs:
        grouped[r.exam_subject.subject.name].append(r)
    return {"title": "Subject Results", "exam": exam, "groups": dict(grouped)}


def _mark_sheet(exam, params):
    exam_subject = ExamSubject.objects.filter(pk=params.get("exam_subject")).first()
    if exam_subject is None:
        from apps.core.exceptions import BusinessRuleError

        raise BusinessRuleError("exam_subject parameter is required.", code="VALIDATION_ERROR")
    components = list(exam_subject.components.filter(status="ACTIVE"))
    marks = {
        (m.exam_candidate_id, m.component_id): m
        for m in Mark.objects.filter(component__in=components)
    }
    enrollments = (
        ExaminationCandidate.objects.filter(
            examination=exam, status__in=ExaminationCandidate.PARTICIPATING
        )
        .select_related("candidate", "school")
        .order_by("school__school_code", "candidate_number")
    )
    return {
        "title": f"Mark Sheet — {exam_subject.subject.name}",
        "exam": exam,
        "exam_subject": exam_subject,
        "components": components,
        "rows": [
            {
                "enrollment": e,
                "marks": [marks.get((e.pk, c.pk)) for c in components],
            }
            for e in enrollments
        ],
    }


def _school_results(exam, params):
    results = _exam_results(exam)
    groups = defaultdict(list)
    for r in results:
        groups[r.exam_candidate.school].append(r)
    return {
        "title": "School Results",
        "exam": exam,
        "groups": sorted(groups.items(), key=lambda kv: kv[0].school_code),
    }


def _position_report(exam, params):
    return {
        "title": "Candidate Position Report",
        "exam": exam,
        "rows": [r for r in _exam_results(exam) if r.position is not None],
    }


def _subject_positions(exam, params):
    qs = _subject_results(exam)
    grouped = defaultdict(list)
    for r in qs:
        grouped[r.exam_subject.subject.name].append(r)
    return {"title": "Subject Positions", "exam": exam, "groups": dict(grouped)}


def _school_summary(exam, params):
    from apps.analytics.services import _pass_grades

    pass_grades = set(_pass_grades(exam))
    results = _exam_results(exam)
    groups = defaultdict(list)
    for r in results:
        groups[r.exam_candidate.school].append(r)
    summaries = []
    for school, rows in sorted(groups.items(), key=lambda kv: kv[0].school_code):
        scored = [r for r in rows if r.status == ResultStatus.SCORED]
        passed = [r for r in scored if r.grade in pass_grades]
        summaries.append(
            {
                "school": school,
                "candidates": len(rows),
                "scored": len(scored),
                "average": round(
                    sum(float(r.average_percentage) for r in scored) / len(scored), 2
                )
                if scored
                else None,
                "pass_rate": round(len(passed) / len(scored) * 100, 1) if scored else None,
                "absent": len([r for r in rows if r.status == ResultStatus.ABSENT]),
                "incomplete": len([r for r in rows if r.status == ResultStatus.INCOMPLETE]),
            }
        )
    return {"title": "School Summary", "exam": exam, "summaries": summaries}


def _exam_summary(exam, params):
    from apps.analytics.services import exam_dashboard

    return {
        "title": "Examination Summary",
        "exam": exam,
        "stats": exam_dashboard(exam),
    }


def _grade_distribution(exam, params):
    rows = (
        CandidateExamResult.objects.filter(examination=exam, status=ResultStatus.SCORED)
        .values("grade")
        .annotate(count=Count("pk"))
        .order_by("grade")
    )
    by_school = (
        CandidateExamResult.objects.filter(examination=exam, status=ResultStatus.SCORED)
        .values("grade", "exam_candidate__school__school_name")
        .annotate(count=Count("pk"))
    )
    return {
        "title": "Grade Distribution",
        "exam": exam,
        "overall": list(rows),
        "by_school": list(by_school),
    }


def _performance_analysis(exam, params):
    from apps.analytics.services import exam_analytics

    return {"title": "Performance Analysis", "exam": exam, "analytics": exam_analytics(exam)}


def _missing_marks(exam, params):
    components = list(
        exam.exam_subjects.filter(status="ACTIVE").prefetch_related("components")
    )
    all_components = [
        c for es in components for c in es.components.filter(status="ACTIVE")
    ]
    if not all_components:
        all_components = list(
            ExamSubject.objects.filter(
                examination=exam, status="ACTIVE"
            ).values_list("pk", flat=True)
        )
    marks_qs = Mark.objects.filter(
        exam_candidate__examination=exam,
        status__in=(Mark.Status.PENDING, Mark.Status.MISSING),
    ).select_related("exam_candidate__candidate", "exam_candidate__school", "component__exam_subject__subject")
    rows = list(marks_qs)
    # Enrolled candidates with no mark at all on required components
    entered_pairs = set(
        Mark.objects.filter(exam_candidate__examination=exam)
        .values_list("exam_candidate_id", "component_id")
    )
    for e in ExaminationCandidate.objects.filter(
        examination=exam, status__in=ExaminationCandidate.PARTICIPATING
    ).select_related("candidate", "school").iterator():
        for comp in all_components:
            if comp.required and (e.pk, comp.pk) not in entered_pairs:
                rows.append(
                    {
                        "candidate_number": e.candidate_number,
                        "candidate_name": e.candidate.full_name,
                        "school": e.school.school_name,
                        "subject": comp.exam_subject.subject.name,
                        "component": comp.code,
                        "status": "MISSING",
                    }
                )
    return {"title": "Missing Marks Report", "exam": exam, "rows": rows}


def _absent_candidates(exam, params):
    qs = CandidateExamResult.objects.filter(
        examination=exam, status=ResultStatus.ABSENT
    ).select_related("exam_candidate__candidate", "exam_candidate__school")
    return {"title": "Absent Candidates", "exam": exam, "rows": list(qs)}


def _marks_completion(exam, params):
    from apps.marks.services import MarksService

    rows = []
    for es in exam.exam_subjects.filter(status="ACTIVE").select_related("subject"):
        comp = MarksService.completion(es)
        rows.append({"subject": es.subject.name, **comp})
    return {"title": "Marks Completion Report", "exam": exam, "rows": rows}


def _audit_report(exam, params):
    from apps.audit.models import AuditLog

    rows = list(
        AuditLog.objects.filter(entity_id=str(exam.pk))
        .order_by("-created_at")
        .values("action", "actor__email", "created_at", "metadata")[:1000]
    )
    return {"title": "Audit Report", "exam": exam, "rows": rows}


_BUILDERS = {
    "CANDIDATE_RESULT": _candidate_result,
    "RESULT_SLIP": _result_slip,
    "FULL_RESULTS": _full_results,
    "SUBJECT_RESULTS": _subject_results_report,
    "MARK_SHEET": _mark_sheet,
    "SCHOOL_RESULTS": _school_results,
    "POSITION_REPORT": _position_report,
    "SUBJECT_POSITIONS": _subject_positions,
    "SCHOOL_SUMMARY": _school_summary,
    "EXAM_SUMMARY": _exam_summary,
    "GRADE_DISTRIBUTION": _grade_distribution,
    "PERFORMANCE_ANALYSIS": _performance_analysis,
    "MISSING_MARKS": _missing_marks,
    "ABSENT_CANDIDATES": _absent_candidates,
    "MARKS_COMPLETION": _marks_completion,
    "AUDIT": _audit_report,
}
