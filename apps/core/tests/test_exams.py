from django.test import TestCase

from apps.core.exceptions import BusinessRuleError
from apps.enrollment.models import ExaminationCandidate
from apps.enrollment.services import ExamEnrollmentService
from apps.examinations.models import ExamComponent, Examination, ExamSubject
from apps.examinations.services import ExaminationService, ExamSubjectService
from apps.subjects.models import Subject

from .fixtures import (
    full_school_setup,
    make_candidate,
    make_candidate_list,
    make_exam,
    make_exam_subject,
    make_school,
)


class ExaminationWorkflowTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.exam = make_exam(self.setup["school"], scheme=self.setup["scheme"])

    def test_valid_transition(self):
        c = make_candidate(self.setup["school"], "C-1")
        lst = make_candidate_list(self.setup["school"], "L1", [c])
        ExamEnrollmentService.enroll_lists(self.exam, [lst.pk])
        subject = Subject.objects.create(
            school=self.setup["school"], name="Math", code="MATH"
        )
        make_exam_subject(self.exam, subject)
        ExaminationService.transition(self.exam, Examination.Status.READY)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, Examination.Status.READY)

    def test_ready_requires_subjects_and_candidates(self):
        with self.assertRaises(BusinessRuleError):
            ExaminationService.transition(self.exam, Examination.Status.READY)

    def test_invalid_transition_rejected(self):
        with self.assertRaises(BusinessRuleError):
            ExaminationService.transition(self.exam, Examination.Status.PUBLISHED)

    def test_archived_is_terminal(self):
        exam = self.exam
        # can't walk to ARCHIVED without content; drive manually via states
        exam.status = Examination.Status.PUBLISHED
        exam.save()
        ExaminationService.transition(exam, Examination.Status.ARCHIVED)
        self.exam.refresh_from_db()
        with self.assertRaises(BusinessRuleError):
            ExaminationService.transition(self.exam, Examination.Status.DRAFT)


class EnrollmentServiceTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.school = self.setup["school"]
        self.exam = make_exam(self.school)
        self.exam.participating_schools.add(self.school)

    def test_enroll_list_creates_candidates(self):
        cands = [make_candidate(self.school, f"S-{i:03d}") for i in range(5)]
        lst = make_candidate_list(self.school, "L1", cands)
        stats = ExamEnrollmentService.enroll_lists(self.exam, [lst.pk])
        self.assertEqual(stats["created"], 5)
        numbers = list(
            ExaminationCandidate.objects.values_list("candidate_number", flat=True)
        )
        self.assertEqual(len(set(numbers)), 5)

    def test_duplicate_enroll_is_idempotent(self):
        cands = [make_candidate(self.school, f"S-{i:03d}") for i in range(5)]
        lst = make_candidate_list(self.school, "L1", cands)
        ExamEnrollmentService.enroll_lists(self.exam, [lst.pk])
        stats = ExamEnrollmentService.enroll_lists(self.exam, [lst.pk])
        self.assertEqual(stats["created"], 0)
        self.assertEqual(stats["already_enrolled"], 5)
        self.assertEqual(ExaminationCandidate.objects.count(), 5)

    def test_candidate_in_multiple_lists_enrolled_once(self):
        cands = [make_candidate(self.school, f"S-{i:03d}") for i in range(4)]
        la = make_candidate_list(self.school, "LA", cands)
        lb = make_candidate_list(self.school, "LB", cands)
        stats = ExamEnrollmentService.enroll_lists(self.exam, [la.pk, lb.pk])
        self.assertEqual(stats["created"], 4)
        self.assertEqual(ExaminationCandidate.objects.count(), 4)

    def test_list_from_nonparticipating_school_rejected(self):
        other = make_school("OUT", "Other School")
        cands = [make_candidate(other, "X-1")]
        lst = make_candidate_list(other, "LO", cands)
        with self.assertRaises(BusinessRuleError):
            ExamEnrollmentService.enroll_lists(self.exam, [lst.pk])

    def test_multi_school_enrollment(self):
        school_b = make_school("BBB", "School B")
        self.exam.participating_schools.add(school_b)
        cands_a = [make_candidate(self.school, f"A-{i:02d}") for i in range(3)]
        cands_b = [make_candidate(school_b, f"B-{i:02d}") for i in range(2)]
        la = make_candidate_list(self.school, "LA", cands_a)
        lb = make_candidate_list(school_b, "LB", cands_b)
        stats = ExamEnrollmentService.enroll_lists(self.exam, [la.pk, lb.pk])
        self.assertEqual(stats["created"], 5)
        self.assertEqual(
            ExaminationCandidate.objects.filter(school=school_b).count(), 2
        )

    def test_preview_does_not_persist(self):
        cands = [make_candidate(self.school, f"P-{i:02d}") for i in range(3)]
        lst = make_candidate_list(self.school, "LP", cands)
        preview = ExamEnrollmentService.preview(self.exam, list_ids=[lst.pk])
        self.assertEqual(preview["would_create"], 3)
        self.assertEqual(ExaminationCandidate.objects.count(), 0)


class ExamSubjectComponentTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.exam = make_exam(self.setup["school"])
        self.subject = Subject.objects.create(
            school=self.setup["school"], name="Math", code="MATH"
        )

    def test_default_component_created(self):
        es = ExamSubject.objects.create(
            examination=self.exam, subject=self.subject, maximum_marks=100
        )
        component = ExamSubjectService.ensure_default_component(es)
        self.assertEqual(component.maximum_marks, 100)

    def test_weighted_components_must_sum_100(self):
        es = make_exam_subject(
            self.exam, self.subject, method=ExamSubject.CalculationMethod.WEIGHTED,
            components=[("P1", "P1", 80, 40), ("P2", "P2", 70, 30)],
        )
        with self.assertRaises(BusinessRuleError):
            ExamSubjectService.validate_component_configuration(es)
        ExamComponent.objects.filter(exam_subject=es, code="P2").update(weight=60)
        self.assertTrue(ExamSubjectService.validate_component_configuration(es))

    def test_structure_endpoint_shape(self):
        c = make_candidate(self.setup["school"], "ST-1")
        lst = make_candidate_list(self.setup["school"], "SL", [c])
        ExamEnrollmentService.enroll_lists(self.exam, [lst.pk])
        make_exam_subject(self.exam, self.subject, components=[("Final", "F", 100, None)])
        structure = ExaminationService.structure(self.exam)
        self.assertEqual(structure["total_candidates"], 1)
        self.assertEqual(len(structure["subjects"]), 1)
        self.assertEqual(len(structure["subjects"][0]["components"]), 1)
        self.assertIn("participating_schools", structure)
