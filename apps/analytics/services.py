from decimal import Decimal

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import Coalesce

from apps.examinations.models import Examination
from apps.marks.models import Mark
from apps.results.models import (
    CandidateExamResult,
    CandidateSubjectResult,
    ResultStatus,
)


def exam_scope(request):
    from apps.accounts.permissions import accessible_school_ids

    school_ids = accessible_school_ids(request)
    return Examination.objects.filter(
        Q(school__in=school_ids) | Q(participating_schools__in=school_ids)
    ).distinct()


def dashboard(request):
    """Dashboard: exam counts, candidate volume, pending marks, per-school
    breakdowns and monthly series for charts."""
    from collections import Counter

    from apps.accounts.permissions import accessible_school_ids
    from apps.audit.models import AuditLog
    from apps.enrollment.models import ExaminationCandidate

    school_ids = accessible_school_ids(request)
    exams = exam_scope(request)
    candidates_qs = ExaminationCandidate.objects.filter(
        Q(examination__school__in=school_ids)
        | Q(examination__participating_schools__in=school_ids),
        status__in=ExaminationCandidate.PARTICIPATING,
    )
    marks = Mark.objects.filter(
        Q(exam_candidate__examination__school__in=school_ids)
        | Q(exam_candidate__examination__participating_schools__in=school_ids)
    )
    exams_by_month = [
        {"month": m, "count": c}
        for m, c in Counter(
            e.created_at.strftime("%Y-%m") for e in exams.only("created_at")
        ).most_common()
    ]
    exams_by_month.sort(key=lambda x: x["month"])
    status_counts = dict(
        Counter(
            exams.values_list("status", flat=True)
        )
    )
    candidates_by_school = [
        {"school": name, "count": c}
        for name, c in Counter(
            candidates_qs.values_list("school__school_name", flat=True)
        ).most_common(10)
    ]
    recent = list(
        exams.order_by("-created_at").values("id", "name", "code", "status", "created_at")[:10]
    )
    activity = list(
        AuditLog.objects.filter(
            Q(school_id__in=school_ids) | Q(entity_type__icontains="examination")
        )
        .order_by("-created_at")
        .values("action", "entity_type", "entity_id", "created_at")[:20]
    )
    return {
        "examinations": {
            "total": exams.count(),
            "draft": exams.filter(status=Examination.Status.DRAFT).count(),
            "active": exams.filter(
                status__in=(
                    Examination.Status.READY,
                    Examination.Status.ACTIVE,
                    Examination.Status.MARKS_ENTRY,
                    Examination.Status.UNDER_REVIEW,
                )
            ).count(),
            "finalized": exams.filter(status=Examination.Status.FINALIZED).count(),
            "published": exams.filter(status=Examination.Status.PUBLISHED).count(),
        },
        "total_candidates": candidates_qs.count(),
        "total_schools": exams.values("school").distinct().count(),
        "marks": {
            "entered": marks.exclude(status=Mark.Status.PENDING).count(),
            "pending": marks.filter(status=Mark.Status.PENDING).count(),
        },
        "recent_examinations": recent,
        "recent_activity": activity,
        "exams_by_month": exams_by_month,
        "exams_by_status": [
            {"status": k, "count": v} for k, v in sorted(status_counts.items())
        ],
        "candidates_by_school": candidates_by_school,
    }


def exam_dashboard(exam, request=None):
    """Per-examination dashboard."""
    candidates_qs = exam.candidates.all()
    active_candidates = candidates_qs.filter(status__in=ExaminationCandidate.PARTICIPATING)
    exam_subjects = exam.exam_subjects.filter(status="ACTIVE").prefetch_related("components")
    component_count = sum(es.components.filter(status="ACTIVE").count() or 1 for es in exam_subjects)

    marks = Mark.objects.filter(exam_candidate__examination=exam)
    entered = marks.exclude(status=Mark.Status.PENDING).count()
    expected = active_candidates.count() * max(component_count, 1)
    results = CandidateExamResult.objects.filter(examination=exam)
    scored = results.filter(status=ResultStatus.SCORED)

    agg = scored.aggregate(
        average=Avg("average_percentage"),
        highest=Max("average_percentage"),
        lowest=Min("average_percentage"),
        total_score=Coalesce(Sum("total_score"), Decimal("0")),
    )
    grade_dist = {
        row["grade"]: row["n"]
        for row in scored.values("grade").annotate(n=Count("pk"))
    }
    completion = [
        {
            "exam_subject_id": str(es.pk),
            "subject": es.subject.name,
            "marks_status": es.marks_status,
        }
        for es in exam_subjects
    ]
    from apps.marks.services import MarksService

    for row in completion:
        es = next(e for e in exam_subjects if str(e.pk) == row["exam_subject_id"])
        row.update(MarksService.completion(es))

    return {
        "examination": {"id": str(exam.pk), "name": exam.name, "code": exam.code, "status": exam.status},
        "candidates": {
            "total": candidates_qs.count(),
            "active": active_candidates.count(),
            "absent": candidates_qs.filter(status="ABSENT").count(),
            "withdrawn": candidates_qs.filter(status="WITHDRAWN").count(),
        },
        "schools": exam.participating_schools.count() or 1,
        "subjects": exam_subjects.count(),
        "marks_completion_percent": round(entered / expected * 100, 1) if expected else 0,
        "marks_entered": entered,
        "marks_expected": expected,
        "results": {
            "scored": scored.count(),
            "incomplete": results.filter(status=ResultStatus.INCOMPLETE).count(),
            "absent": results.filter(status=ResultStatus.ABSENT).count(),
        },
        "average_percentage": _num(agg["average"]),
        "highest_average": _num(agg["highest"]),
        "lowest_average": _num(agg["lowest"]),
        "grade_distribution": grade_dist,
        "subject_completion": completion,
        "published": exam.status == Examination.Status.PUBLISHED,
    }


def _num(value):
    return round(float(value), 2) if value is not None else None


def exam_analytics(exam):
    """Deeper analytics: subject performance, school statistics."""
    subjects = (
        CandidateSubjectResult.objects.filter(
            examination=exam, status=ResultStatus.SCORED
        )
        .values("exam_subject__subject__name")
        .annotate(
            average=Avg("percentage"),
            highest=Max("percentage"),
            lowest=Min("percentage"),
            candidates=Count("pk"),
            passed=Count("pk", filter=Q(is_pass=True)),
        )
        .order_by("-average")
    )
    subject_rows = [
        {
            "subject": s["exam_subject__subject__name"],
            "candidates": s["candidates"],
            "average": _num(s["average"]),
            "highest": _num(s["highest"]),
            "lowest": _num(s["lowest"]),
            "pass_rate": round(s["passed"] / s["candidates"] * 100, 1) if s["candidates"] else 0,
        }
        for s in subjects
    ]
    schools = (
        CandidateExamResult.objects.filter(examination=exam)
        .values("exam_candidate__school__school_name", "exam_candidate__school_id")
        .annotate(
            candidates=Count("pk"),
            scored=Count("pk", filter=Q(status=ResultStatus.SCORED)),
            average=Avg("average_percentage", filter=Q(status=ResultStatus.SCORED)),
        )
    )
    pass_stats = (
        CandidateExamResult.objects.filter(
            examination=exam, status=ResultStatus.SCORED
        )
        .values("exam_candidate__school_id")
        .annotate(
            passed=Count(
                "pk",
                filter=Q(grade__in=_pass_grades(exam)),
            ),
        )
    )
    pass_by_school = {r["exam_candidate__school_id"]: r["passed"] for r in pass_stats}
    school_rows = [
        {
            "school_id": str(s["exam_candidate__school_id"]),
            "school": s["exam_candidate__school__school_name"],
            "candidates": s["candidates"],
            "scored": s["scored"],
            "average": _num(s["average"]),
            "pass_rate": round(pass_by_school.get(s["exam_candidate__school_id"], 0) / s["scored"] * 100, 1)
            if s["scored"] else 0,
        }
        for s in schools
    ]
    return {
        "subjects": subject_rows,
        "schools": school_rows,
        "grade_distribution": {
            row["grade"]: row["n"]
            for row in CandidateExamResult.objects.filter(
                examination=exam, status=ResultStatus.SCORED
            ).values("grade").annotate(n=Count("pk"))
        },
        "gender_distribution": {
            row["exam_candidate__candidate__gender"]: row["n"]
            for row in CandidateExamResult.objects.filter(examination=exam)
            .values("exam_candidate__candidate__gender")
            .annotate(n=Count("pk"))
        },
    }


def _pass_grades(exam):
    scheme = exam.grading_scheme
    if scheme is None:
        from apps.grading.models import GradingScheme

        scheme = GradingScheme.objects.filter(school=exam.school, is_default=True).first()
    if scheme is None:
        return ()
    return tuple(scheme.bands.filter(is_pass=True).values_list("grade", flat=True))


def compare_examinations(exam_a, exam_b):
    """Exam-vs-exam comparison on averages, pass rates, subjects, schools."""
    def _stats(exam):
        results = CandidateExamResult.objects.filter(
            examination=exam, status=ResultStatus.SCORED
        )
        agg = results.aggregate(average=Avg("average_percentage"), count=Count("pk"))
        pass_grades = _pass_grades(exam)
        passed = results.filter(grade__in=pass_grades).count()
        subjects = {
            r["exam_subject__subject__code"]: {
                "name": r["exam_subject__subject__name"],
                "average": _num(r["average"]),
                "count": r["n"],
            }
            for r in CandidateSubjectResult.objects.filter(
                examination=exam, status=ResultStatus.SCORED
            )
            .values("exam_subject__subject__code", "exam_subject__subject__name")
            .annotate(average=Avg("percentage"), n=Count("pk"))
        }
        return {
            "examination": {"id": str(exam.pk), "name": exam.name, "code": exam.code},
            "candidates": agg["count"],
            "average": _num(agg["average"]),
            "pass_rate": round(passed / agg["count"] * 100, 1) if agg["count"] else 0,
            "subjects": subjects,
        }

    a, b = _stats(exam_a), _stats(exam_b)
    shared = sorted(set(a["subjects"]) & set(b["subjects"]))
    return {
        "a": a,
        "b": b,
        "average_delta": _num((a["average"] or 0) - (b["average"] or 0))
        if a["average"] is not None and b["average"] is not None else None,
        "subject_comparison": [
            {
                "subject": code,
                "a_average": a["subjects"][code]["average"],
                "b_average": b["subjects"][code]["average"],
                "delta": round(
                    (a["subjects"][code]["average"] or 0) - (b["subjects"][code]["average"] or 0), 2
                ),
            }
            for code in shared
        ],
    }
