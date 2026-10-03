"""Development seed: two schools, candidate lists, a joint examination
with marks, results finalized and published. Idempotent."""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role, SchoolMembership, User
from apps.candidate_lists.models import CandidateList
from apps.candidates.models import Candidate
from apps.enrollment.services import ExamEnrollmentService
from apps.examinations.models import Examination, ExamSubject
from apps.examinations.services import ExamSubjectService, ExaminationService
from apps.grading.models import DivisionBand, GradeBand, GradingScheme
from apps.marks.services import MarksService
from apps.results.services import (
    ResultCalculationService,
    ResultPublicationService,
)
from apps.schools.models import School
from apps.subjects.models import Subject

SUBJECTS = [
    ("Mathematics", "MATH", [("Paper 1", "P1", 80), ("Paper 2", "P2", 70)]),
    ("English", "ENG", [("Paper 1", "P1", 60), ("Writing", "WRT", 40)]),
    ("Kiswahili", "KIS", [("Final", "FINAL", 100)]),
    ("Biology", "BIO", [("Theory", "TH", 70), ("Practical", "PRAC", 30)]),
]

SCHOOLS = [
    ("Demo Secondary School", "DEMO-01"),
    ("St Mary High School", "STM-02"),
]


class Command(BaseCommand):
    help = "Seed demo data for development."

    def add_arguments(self, parser):
        parser.add_argument("--candidates", type=int, default=8,
                            help="Candidates per school")
        parser.add_argument("--no-publish", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        n = options["candidates"]

        admin, _ = User.objects.get_or_create(
            email="admin@demo.emas.local",
            defaults={
                "first_name": "Demo", "last_name": "Admin",
                "is_superadmin": True, "is_superuser": True, "is_staff": True,
            },
        )
        if not admin.has_usable_password() or not admin.check_password("admin12345"):
            admin.set_password("admin12345")
            admin.save()

        marks_user, _ = User.objects.get_or_create(
            email="entry@demo.emas.local",
            defaults={"first_name": "Marks", "last_name": "Entry"},
        )
        if not marks_user.check_password("entry12345"):
            marks_user.set_password("entry12345")
            marks_user.save()

        schools = []
        for i, (name, code) in enumerate(SCHOOLS):
            school, _ = School.objects.get_or_create(
                school_code=code, defaults={"school_name": name}
            )
            SchoolMembership.objects.get_or_create(
                user=admin, school=school, defaults={"role": Role.EXAM_ADMIN}
            )
            if i == 0:
                SchoolMembership.objects.get_or_create(
                    user=marks_user, school=school,
                    defaults={"role": Role.MARKS_ENTRY},
                )
            schools.append(school)

        scheme, _ = GradingScheme.objects.get_or_create(
            school=schools[0], name="Standard ECDECEF", defaults={"is_default": True}
        )
        if not scheme.bands.exists():
            for grade, lo, hi, pts, is_pass, remark in [
                ("A", 80, 100, 1, True, "Excellent"),
                ("B", 70, 79.99, 2, True, "Very good"),
                ("C", 60, 69.99, 3, True, "Good"),
                ("D", 50, 59.99, 4, True, "Satisfactory"),
                ("E", 40, 49.99, 5, False, "Marginal"),
                ("F", 0, 39.99, 6, False, "Fail"),
            ]:
                GradeBand.objects.create(
                    scheme=scheme, grade=grade,
                    min_percentage=Decimal(str(lo)), max_percentage=Decimal(str(hi)),
                    points=pts, is_pass=is_pass, remark=remark,
                )
            DivisionBand.objects.create(scheme=scheme, name="I", min_points=0, max_points=17)
            DivisionBand.objects.create(scheme=scheme, name="II", min_points=18, max_points=21)
            DivisionBand.objects.create(scheme=scheme, name="III", min_points=22, max_points=25)
            DivisionBand.objects.create(scheme=scheme, name="IV", min_points=26, max_points=60)

        subjects = {}
        for name, code, _components in SUBJECTS:
            subject, _ = Subject.objects.get_or_create(
                school=schools[0], code=code,
                defaults={"name": name, "short_name": name[:12]},
            )
            subjects[code] = subject

        # Candidate lists per school
        candidate_lists = []
        all_candidates = []
        for si, school in enumerate(schools):
            lst, _ = CandidateList.objects.get_or_create(
                school=school, name=f"{school.school_name} — Form Four 2026"
            )
            for i in range(1, n + 1):
                cand, created = Candidate.objects.get_or_create(
                    school=school,
                    candidate_number=f"{school.school_code}-{i:04d}",
                    defaults={
                        "first_name": f"Candidate{i}",
                        "last_name": f"S{si}",
                        "gender": "MALE" if i % 2 else "FEMALE",
                        "guardian_name": f"Guardian {i}",
                        "guardian_phone": f"+255700{i:06d}",
                    },
                )
                lst.candidates.add(cand)
                all_candidates.append(cand)
            candidate_lists.append(lst)

        exam, _ = Examination.objects.get_or_create(
            school=schools[0], code="JSE-2026-01",
            defaults={
                "name": "Joint School Examination 2026",
                "description": "Annual joint examination across participating schools",
                "grading_scheme": scheme,
            },
        )
        exam.participating_schools.set(schools)

        for i, (name, code, components) in enumerate(SUBJECTS):
            exam_subject, _ = ExamSubject.objects.get_or_create(
                examination=exam, subject=subjects[code],
                defaults={"maximum_marks": 100, "pass_mark": 50, "display_order": i},
            )
            if not exam_subject.components.exists():
                from apps.examinations.models import ExamComponent

                for cname, ccode, cmax in components:
                    ExamComponent.objects.create(
                        exam_subject=exam_subject, name=cname, code=ccode,
                        maximum_marks=cmax,
                    )

        marks_entered = 0
        if exam.status in (Examination.Status.DRAFT, Examination.Status.READY):
            stats = ExamEnrollmentService.enroll_lists(
                exam, [l.pk for l in candidate_lists], actor=admin
            )
            self.stdout.write(f"Enrolled {stats['created']} candidates")

        # Marks entry: deterministic marks for every candidate/component
        if exam.status == Examination.Status.DRAFT:
            exam = ExaminationService.transition(exam, Examination.Status.READY, actor=admin)
        if exam.status == Examination.Status.READY:
            exam = ExaminationService.transition(exam, Examination.Status.ACTIVE, actor=admin)
            exam = ExaminationService.transition(exam, Examination.Status.MARKS_ENTRY, actor=admin)

        if exam.status == Examination.Status.MARKS_ENTRY:
            import random

            rng = random.Random(42)
            for ec in exam.candidates.filter(status="ACTIVE").select_related("school"):
                for exam_subject in exam.exam_subjects.all():
                    for comp in exam_subject.components.filter(status="ACTIVE"):
                        MarksService.set_mark(
                            ec, comp,
                            value=Decimal(rng.randint(35, 98) * comp.maximum_marks // 100),
                            actor=marks_user,
                        )
                        marks_entered += 1
            self.stdout.write(f"Entered {marks_entered} marks")

        if exam.status in (Examination.Status.MARKS_ENTRY, Examination.Status.UNDER_REVIEW):
            ResultCalculationService.calculate_exam(exam, actor=admin)
        if not options["no_publish"]:
            if exam.status == Examination.Status.MARKS_ENTRY:
                exam = ExaminationService.transition(exam, Examination.Status.UNDER_REVIEW, actor=admin)
            if exam.status == Examination.Status.UNDER_REVIEW:
                exam = ResultPublicationService.finalize(exam, actor=admin)
            if exam.status == Examination.Status.FINALIZED:
                ResultPublicationService.publish(exam, actor=admin)
            exam.refresh_from_db()

        self.stdout.write(self.style.SUCCESS(
            f"Seeded: {len(schools)} schools, {len(all_candidates)} candidates, "
            f"exam={exam.code} ({exam.status}), {marks_entered} marks. "
            "Login: admin@demo.emas.local / admin12345"
        ))
