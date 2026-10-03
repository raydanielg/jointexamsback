from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core.exceptions import BusinessRuleError
from apps.candidates.models import Candidate
from apps.imports.models import ImportSession
from apps.imports.services import ImportService
from apps.marks.models import Mark
from apps.marks.services import MarksService
from apps.results.services import ResultCalculationService
from apps.results.models import CandidateExamResult

from .fixtures import full_school_setup, make_exam_with_candidates


def csv_upload(rows, headers):
    content = "\n".join([",".join(headers)] + [",".join(map(str, r)) for r in rows])
    return SimpleUploadedFile("candidates.csv", content.encode(), content_type="text/csv")


CANDIDATE_HEADERS = [
    "candidate_number", "first_name", "last_name", "gender", "guardian_phone"
]


class CandidateImportTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.school = self.setup["school"]

    def _session(self, file, strict=True):
        return ImportSession.objects.create(
            school=self.school,
            import_type=ImportSession.ImportType.CANDIDATES,
            file=file,
            strict=strict,
            created_by=self.setup["admin"],
        )

    def test_valid_import(self):
        session = self._session(csv_upload([
            ["IMP-1", "Asha", "Mushi", "FEMALE", "+255700000001"],
            ["IMP-2", "John", "Doe", "MALE", ""],
        ], CANDIDATE_HEADERS))
        ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.status, ImportSession.Status.COMPLETED)
        self.assertEqual(session.success_rows, 2)
        self.assertEqual(Candidate.objects.filter(school=self.school).count(), 2)

    def test_duplicate_number_rejected(self):
        session = self._session(csv_upload([
            ["IMP-1", "Asha", "Mushi", "FEMALE", ""],
            ["IMP-1", "John", "Doe", "MALE", ""],
        ], CANDIDATE_HEADERS), strict=False)
        ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.success_rows, 1)
        self.assertEqual(session.duplicate_rows, 1)
        self.assertEqual(Candidate.objects.count(), 1)

    def test_strict_mode_aborts_on_errors(self):
        session = self._session(csv_upload([
            ["IMP-9", "Asha", "Mushi", "FEMALE", ""],
            ["IMP-10", "John", "Doe", "BADGENDER", ""],
        ], CANDIDATE_HEADERS), strict=True)
        with self.assertRaises(BusinessRuleError):
            ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.status, ImportSession.Status.FAILED)
        self.assertEqual(Candidate.objects.count(), 0)

    def test_missing_name_rejected(self):
        session = self._session(csv_upload([
            ["IMP-30", "", "Mushi", "FEMALE", ""],
        ], CANDIDATE_HEADERS), strict=False)
        ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.success_rows, 0)
        self.assertEqual(session.failed_rows, 1)
        self.assertTrue(session.error_report)

    def test_invalid_phone_rejected(self):
        session = self._session(csv_upload([
            ["IMP-40", "Asha", "Mushi", "FEMALE", "abc"],
        ], CANDIDATE_HEADERS), strict=False)
        ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.failed_rows, 1)


class MarksImportTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.exam, self.candidates, self.subjects = make_exam_with_candidates(
            self.setup, n=3, subjects={"MATH": {"components": [("Paper 1", "P1", 100, None)]}}
        )
        self.exam_subject = self.subjects["MATH"]

    def _session(self, rows, strict=True):
        headers = ["candidate_number", "subject_code", "component_code", "marks"]
        file = csv_upload(rows, headers)
        return ImportSession.objects.create(
            school=self.setup["school"],
            import_type=ImportSession.ImportType.MARKS,
            file=file,
            strict=strict,
            params={"examination": str(self.exam.pk)},
            created_by=self.setup["admin"],
        )

    def test_marks_import(self):
        rows = [[c.candidate_number, "MATH", "P1", 60 + i] for i, c in enumerate(self.candidates)]
        session = self._session(rows)
        ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.success_rows, 3)
        self.assertEqual(Mark.objects.count(), 3)

    def test_unknown_candidate_rejected(self):
        session = self._session([["NOPE-1", "MATH", "P1", 60]], strict=False)
        ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.failed_rows, 1)
        self.assertEqual(Mark.objects.count(), 0)

    def test_mark_out_of_range_rejected(self):
        session = self._session(
            [[self.candidates[0].candidate_number, "MATH", "P1", 999]], strict=False
        )
        ImportService.process(session)
        session.refresh_from_db()
        self.assertEqual(session.failed_rows, 1)
        self.assertEqual(Mark.objects.count(), 0)


class ReportTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()

    def test_report_job_generation(self):
        from apps.reports.models import ReportJob
        from apps.reports.services import ReportGenerationService

        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=3, subjects={"MATH": {}}
        )
        component = subjects["MATH"].components.first()
        for i, ec in enumerate(candidates):
            MarksService.set_mark(ec, component, value=Decimal(50 + i * 10))
        ResultCalculationService.calculate_exam(exam)

        job = ReportGenerationService.create_job(
            self.setup["school"], "FULL_RESULTS", "XLSX", examination=exam
        )
        ReportGenerationService.generate(job)
        job.refresh_from_db()
        self.assertEqual(job.status, ReportJob.Status.COMPLETED)
        self.assertTrue(job.file)
        self.assertTrue(job.file.size > 100)

    def test_position_report_csv(self):
        from apps.reports.services import ReportGenerationService

        exam, candidates, subjects = make_exam_with_candidates(
            self.setup, n=2, subjects={"MATH": {}}, exam_code="RPT"
        )
        component = subjects["MATH"].components.first()
        for i, ec in enumerate(candidates):
            MarksService.set_mark(ec, component, value=Decimal(60 + i))
        ResultCalculationService.calculate_exam(exam)
        job = ReportGenerationService.create_job(
            self.setup["school"], "POSITION_REPORT", "CSV", examination=exam
        )
        ReportGenerationService.generate(job)
        content = job.file.read().decode()
        self.assertIn("Position", content)
        self.assertIn(candidates[0].candidate_number, content)
