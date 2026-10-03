from decimal import Decimal

from django.test import TestCase

from apps.core.exceptions import BusinessRuleError
from apps.examinations.models import ExamSubject
from apps.marks.models import Mark, MarkChangeLog
from apps.marks.services import MarksService
from apps.results.models import (
    CandidateExamResult,
    CandidateSubjectResult,
    ResultStatus,
)
from apps.results.services import ResultCalculationService

from .fixtures import (
    full_school_setup,
    make_candidate,
    make_exam_with_candidates,
)


def _first_component(exam_subject):
    return exam_subject.components.first()


class MarksValidationTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.exam, self.candidates, self.subjects = make_exam_with_candidates(
            self.setup, n=3, subjects={"MATH": {}}
        )
        self.exam_subject = self.subjects["MATH"]
        self.component = _first_component(self.exam_subject)

    def test_valid_mark_accepted(self):
        mark = MarksService.set_mark(self.candidates[0], self.component, value=Decimal("75"))
        self.assertEqual(mark.value, Decimal("75"))
        self.assertEqual(mark.status, Mark.Status.ENTERED)

    def test_negative_mark_rejected(self):
        with self.assertRaises(BusinessRuleError) as ctx:
            MarksService.set_mark(self.candidates[0], self.component, value=Decimal("-1"))
        self.assertEqual(ctx.exception.code, "MARK_OUT_OF_RANGE")

    def test_mark_above_max_rejected(self):
        with self.assertRaises(BusinessRuleError):
            MarksService.set_mark(
                self.candidates[0], self.component,
                value=self.component.maximum_marks + 1,
            )

    def test_zero_and_max_boundary_accepted(self):
        m0 = MarksService.set_mark(self.candidates[0], self.component, value=Decimal("0"))
        self.assertEqual(m0.value, Decimal("0"))
        mmax = MarksService.set_mark(
            self.candidates[1], self.component, value=self.component.maximum_marks
        )
        self.assertEqual(mmax.value, self.component.maximum_marks)

    def test_absent_is_not_zero(self):
        MarksService.set_mark(
            self.candidates[0], self.component, status=Mark.Status.ABSENT
        )
        ResultCalculationService.calculate_exam(self.exam)
        result = CandidateSubjectResult.objects.get(
            exam_candidate=self.candidates[0], exam_subject=self.exam_subject
        )
        self.assertEqual(result.status, ResultStatus.ABSENT)
        self.assertIsNone(result.score)

    def test_missing_mark_stays_incomplete(self):
        ResultCalculationService.calculate_exam(self.exam)
        result = CandidateSubjectResult.objects.get(
            exam_candidate=self.candidates[0], exam_subject=self.exam_subject
        )
        self.assertEqual(result.status, ResultStatus.INCOMPLETE)

    def test_duplicate_mark_updates_not_duplicates(self):
        MarksService.set_mark(self.candidates[0], self.component, value=Decimal("10"))
        MarksService.set_mark(self.candidates[0], self.component, value=Decimal("20"))
        self.assertEqual(Mark.objects.filter(exam_candidate=self.candidates[0]).count(), 1)

    def test_mark_change_logged(self):
        MarksService.set_mark(self.candidates[0], self.component, value=Decimal("10"))
        MarksService.set_mark(
            self.candidates[0], self.component, value=Decimal("40"), reason="correction"
        )
        log = MarkChangeLog.objects.filter(exam_candidate=self.candidates[0]).latest("created_at")
        self.assertEqual(log.old_value, Decimal("10"))
        self.assertEqual(log.new_value, Decimal("40"))

    def test_locked_marks_reject_entry(self):
        self.exam_subject.marks_status = ExamSubject.MarksStatus.LOCKED
        self.exam_subject.save()
        with self.assertRaises(BusinessRuleError) as ctx:
            MarksService.set_mark(self.candidates[0], self.component, value=Decimal("10"))
        self.assertEqual(ctx.exception.code, "MARKS_LOCKED")

    def test_bulk_entry_rolls_back_on_error(self):
        rows = [
            {"candidate_number": self.candidates[0].candidate_number, "component_code": self.component.code, "value": "10"},
            {"candidate_number": self.candidates[1].candidate_number, "component_code": self.component.code, "value": "999"},
        ]
        result = MarksService.bulk_set(self.exam_subject, rows)
        self.assertFalse(result["success"])
        self.assertEqual(Mark.objects.count(), 0)

    def test_withdrawn_candidate_rejected(self):
        self.candidates[0].status = "WITHDRAWN"
        self.candidates[0].save()
        with self.assertRaises(BusinessRuleError):
            MarksService.set_mark(self.candidates[0], self.component, value=Decimal("50"))


class ResultCalculationTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()

    def test_single_component_subject(self):
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=3, subjects={"MATH": {"maximum_marks": 100}}
        )
        component = _first_component(subjects["MATH"])
        MarksService.set_mark(candidates[0], component, value=Decimal("80"))
        MarksService.set_mark(candidates[1], component, value=Decimal("40"))
        ResultCalculationService.calculate_exam(exam)
        r = CandidateSubjectResult.objects.get(exam_candidate=candidates[0])
        self.assertEqual(r.percentage, Decimal("80.00"))
        self.assertEqual(r.grade, "A")
        self.assertEqual(r.position, 1)

    def test_multi_component_normalized(self):
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=1,
            subjects={"MATH": {"maximum_marks": 100,
                               "components": [("P1", "P1", 80, None), ("P2", "P2", 70, None)]}},
        )
        es = subjects["MATH"]
        MarksService.set_mark(candidates[0], es.components.get(code="P1"), value=Decimal("70"))
        MarksService.set_mark(candidates[0], es.components.get(code="P2"), value=Decimal("60"))
        ResultCalculationService.calculate_exam(exam)
        r = CandidateSubjectResult.objects.get(exam_candidate=candidates[0], exam_subject=es)
        # raw = 130/150 -> 86.67%
        self.assertEqual(r.percentage, Decimal("86.67"))
        self.assertEqual(r.grade, "A")

    def test_weighted_components(self):
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=1,
            subjects={"ENG": {"maximum_marks": 100,
                              "method": ExamSubject.CalculationMethod.WEIGHTED,
                              "components": [("P1", "P1", 100, 40), ("P2", "P2", 100, 60)]}},
        )
        es = subjects["ENG"]
        MarksService.set_mark(candidates[0], es.components.get(code="P1"), value=Decimal("50"))
        MarksService.set_mark(candidates[0], es.components.get(code="P2"), value=Decimal("100"))
        ResultCalculationService.calculate_exam(exam)
        r = CandidateSubjectResult.objects.get(exam_candidate=candidates[0], exam_subject=es)
        self.assertEqual(r.percentage, Decimal("80.00"))
        self.assertEqual(r.grade, "A")

    def test_no_marks_is_incomplete(self):
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=2, subjects={"MATH": {}}
        )
        MarksService.set_mark(candidates[0], _first_component(subjects["MATH"]), value=Decimal("60"))
        ResultCalculationService.calculate_exam(exam)
        result = CandidateExamResult.objects.get(exam_candidate=candidates[1])
        self.assertEqual(result.status, ResultStatus.INCOMPLETE)
        self.assertIsNone(result.position)

    def test_recalculation_is_idempotent(self):
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=2, subjects={"MATH": {}}
        )
        MarksService.set_mark(candidates[0], _first_component(subjects["MATH"]), value=Decimal("80"))
        ResultCalculationService.calculate_exam(exam)
        count1 = CandidateSubjectResult.objects.count()
        ResultCalculationService.calculate_exam(exam)
        self.assertEqual(CandidateSubjectResult.objects.count(), count1)

    def test_division_from_best_subjects(self):
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=1, subjects={"MATH": {}, "ENG": {}, "KIS": {}}
        )
        exam.division_best_subjects = 3
        exam.save()
        for es in exam.exam_subjects.all():
            MarksService.set_mark(candidates[0], _first_component(es), value=Decimal("90"))
        ResultCalculationService.calculate_exam(exam)
        result = CandidateExamResult.objects.get(exam_candidate=candidates[0])
        self.assertEqual(result.points_sum, Decimal("3.0"))
        self.assertEqual(result.division, "I")


class RankingTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()

    def test_ties_share_position_competition(self):
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=4, subjects={"MATH": {}}
        )
        component = _first_component(subjects["MATH"])
        for enr, score in zip(candidates, [90, 80, 80, 60]):
            MarksService.set_mark(enr, component, value=Decimal(score))
        ResultCalculationService.calculate_exam(exam)
        positions = sorted(
            CandidateSubjectResult.objects.values_list("percentage", "position")
        )
        self.assertEqual(positions, [
            (Decimal("60.00"), 4),
            (Decimal("80.00"), 2),
            (Decimal("80.00"), 2),
            (Decimal("90.00"), 1),
        ])

    def test_dense_ranking(self):
        from apps.ranking.models import RankingConfiguration
        from apps.ranking.services import rank_items

        class Item:
            def __init__(self, pk, score):
                self.pk = pk
                self.score = score

        items = [Item(1, 90), Item(2, 80), Item(3, 80), Item(4, 60)]
        dense = {i.pk: pos for i, pos in rank_items(items, lambda i: i.score, RankingConfiguration.Method.DENSE)}
        self.assertEqual(dense, {1: 1, 2: 2, 3: 2, 4: 3})
        comp = {i.pk: pos for i, pos in rank_items(items, lambda i: i.score, RankingConfiguration.Method.COMPETITION)}
        self.assertEqual(comp, {1: 1, 2: 2, 3: 2, 4: 4})

    def test_school_positions_scoped(self):
        from .fixtures import make_school

        school_b = make_school("SCB", "School B")
        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=2, subjects={"MATH": {}}, extra_schools=[school_b]
        )
        component = _first_component(subjects["MATH"])
        for i, enr in enumerate(candidates):
            MarksService.set_mark(enr, component, value=Decimal(90 - i * 10))
        ResultCalculationService.calculate_exam(exam)
        school_b_rows = CandidateExamResult.objects.filter(exam_candidate__school=school_b)
        positions = sorted(school_b_rows.values_list("school_position", flat=True))
        self.assertEqual(positions, [1, 2])
