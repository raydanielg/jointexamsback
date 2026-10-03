"""Shared fixtures for EMAS tests: schools, users, candidates, exams."""
from decimal import Decimal

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role, SchoolMembership, User
from apps.candidate_lists.models import CandidateList
from apps.candidates.models import Candidate
from apps.enrollment.services import ExamEnrollmentService
from apps.examinations.models import ExamComponent, Examination, ExamSubject
from apps.examinations.services import ExamSubjectService
from apps.grading.models import DivisionBand, GradeBand, GradingScheme
from apps.schools.models import School
from apps.subjects.models import Subject


def make_school(code="TST", name="Test School"):
    return School.objects.create(school_name=name, school_code=code)


def make_user(email, is_superadmin=False):
    user = User.objects.create_user(
        email=email, password="testpass123", first_name="Test", last_name="User"
    )
    user.is_superadmin = is_superadmin
    user.save()
    return user


def make_membership(user, school, role=Role.EXAM_ADMIN):
    return SchoolMembership.objects.create(user=user, school=school, role=role)


def make_candidate(school, number, first="John", last="Doe", gender="MALE", guardian_phone=""):
    return Candidate.objects.create(
        school=school, candidate_number=number, first_name=first,
        last_name=last, gender=gender, guardian_phone=guardian_phone,
    )


def make_candidate_list(school, name, candidates=()):
    lst = CandidateList.objects.create(school=school, name=name)
    for c in candidates:
        lst.candidates.add(c)
    return lst


def make_scheme(school, name="Default"):
    scheme = GradingScheme.objects.create(school=school, name=name, is_default=True)
    for grade, lo, hi, pts, is_pass in [
        ("A", 80, 100, 1, True), ("B", 70, 79.99, 2, True), ("C", 60, 69.99, 3, True),
        ("D", 50, 59.99, 4, True), ("E", 40, 49.99, 5, False), ("F", 0, 39.99, 6, False),
    ]:
        GradeBand.objects.create(
            scheme=scheme, grade=grade, min_percentage=Decimal(str(lo)),
            max_percentage=Decimal(str(hi)), points=pts, is_pass=is_pass,
        )
    DivisionBand.objects.create(scheme=scheme, name="I", min_points=0, max_points=17)
    DivisionBand.objects.create(scheme=scheme, name="II", min_points=18, max_points=21)
    return scheme


def make_exam(school, code="EX1", name="Test Exam", scheme=None):
    exam = Examination.objects.create(
        school=school, name=name, code=code, grading_scheme=scheme,
    )
    if school not in exam.participating_schools.all():
        exam.participating_schools.add(school)
    return exam


def make_exam_subject(exam, subject, maximum_marks=100, pass_mark=50,
                      method=ExamSubject.CalculationMethod.NORMALIZED, components=None,
                      display_order=0):
    es = ExamSubject.objects.create(
        examination=exam, subject=subject, maximum_marks=maximum_marks,
        pass_mark=pass_mark, calculation_method=method, display_order=display_order,
    )
    if components:
        for i, (cname, ccode, cmax, weight) in enumerate(components):
            ExamComponent.objects.create(
                exam_subject=es, name=cname, code=ccode, maximum_marks=cmax,
                weight=weight, display_order=i,
            )
    else:
        ExamSubjectService.ensure_default_component(es)
    return es


def auth_client(user, school=None):
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
        **({"HTTP_X_SCHOOL_ID": str(school.pk)} if school else {}),
    )
    return client


def full_school_setup(code="TST"):
    """Organizer school + exam admin + grading scheme."""
    school = make_school(code)
    admin = make_user(f"admin@{code}.test")
    make_membership(admin, school, Role.EXAM_ADMIN)
    scheme = make_scheme(school)
    return {"school": school, "admin": admin, "scheme": scheme}


def make_exam_with_candidates(setup, n=5, subjects=None, exam_code="EX1",
                              extra_schools=None, guardian_phone=""):
    """Exam + candidates (+ optional extra participating schools) enrolled
    via one list per school. Returns (exam, exam_candidates, exam_subjects)."""
    school, scheme, admin = setup["school"], setup["scheme"], setup["admin"]
    all_candidates = []
    lists = []
    schools = [school] + list(extra_schools or [])
    for si, sch in enumerate(schools):
        cands = [
            make_candidate(sch, f"{exam_code}-{si}-{i:03d}", guardian_phone=guardian_phone)
            for i in range(1, n + 1)
        ]
        all_candidates.extend(cands)
        lists.append(make_candidate_list(sch, f"{sch.school_code}-List-{exam_code}", cands))
    exam = make_exam(school, code=exam_code, scheme=scheme)
    for sch in schools:
        exam.participating_schools.add(sch)
    exam_subjects = {}
    for i, (code, kwargs) in enumerate((subjects or {"MATH": {}}).items()):
        subj, _ = Subject.objects.get_or_create(school=school, code=code, defaults={"name": code})
        exam_subjects[code] = make_exam_subject(exam, subj, display_order=i, **kwargs)
    ExamEnrollmentService.enroll_lists(exam, [l.pk for l in lists], actor=admin)
    return exam, list(exam.candidates.all()), exam_subjects


def drive_to_status(exam, target, admin):
    """Walk the status machine to `target`."""
    from apps.examinations.services import ExaminationService

    path = [
        Examination.Status.READY,
        Examination.Status.ACTIVE,
        Examination.Status.MARKS_ENTRY,
        Examination.Status.UNDER_REVIEW,
        Examination.Status.FINALIZED,
        Examination.Status.PUBLISHED,
    ]
    for step in path:
        exam = ExaminationService.transition(exam, step, actor=admin)
        if step == target:
            break
    return exam
