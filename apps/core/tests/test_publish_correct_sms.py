from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.core.exceptions import BusinessRuleError
from apps.examinations.models import Examination
from apps.marks.models import Mark, MarkChangeLog
from apps.marks.services import MarksService
from apps.results.models import (
    CandidateSubjectResult,
    ResultCorrectionRequest,
    ResultSnapshot,
)
from apps.results.services import (
    ResultCalculationService,
    ResultCorrectionService,
    ResultPublicationService,
)
from apps.sms.models import SMSCampaign, SMSMessage, SMSTemplate
from apps.sms.providers import BaseSMSProvider
from apps.sms.services import SMSService

from .fixtures import (
    drive_to_status,
    full_school_setup,
    make_exam_with_candidates,
)


def _setup_published(setup, n=3, guardian_phone="+255700000000", exam_code="EX1"):
    exam, candidates, subjects = make_exam_with_candidates(
        setup, n=n, subjects={"MATH": {}}, guardian_phone=guardian_phone, exam_code=exam_code
    )
    component = subjects["MATH"].components.first()
    for i, ec in enumerate(candidates):
        MarksService.set_mark(ec, component, value=Decimal(50 + i * 10))
    ResultCalculationService.calculate_exam(exam, actor=setup["admin"])
    drive_to_status(exam, Examination.Status.UNDER_REVIEW, setup["admin"])
    ResultPublicationService.finalize(exam, actor=setup["admin"])
    ResultPublicationService.publish(exam, actor=setup["admin"])
    exam.refresh_from_db()
    return exam, candidates, subjects, component


class PublishingTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.exam, self.candidates, self.subjects, self.component = _setup_published(self.setup)

    def test_finalize_creates_snapshot(self):
        snap = ResultSnapshot.objects.get(examination=self.exam, kind="FINALIZED")
        self.assertIn("candidates", snap.payload)
        self.assertEqual(snap.version, 1)
        self.assertTrue(snap.grading_scheme_snapshot)

    def test_publish_creates_snapshot(self):
        snap = ResultSnapshot.objects.get(examination=self.exam, kind="PUBLISHED")
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, Examination.Status.PUBLISHED)
        self.assertIsNotNone(self.exam.published_at)

    def test_published_marks_reject_direct_edit(self):
        with self.assertRaises(BusinessRuleError) as ctx:
            MarksService.set_mark(self.candidates[0], self.component, value=Decimal("99"))
        self.assertEqual(ctx.exception.code, "EXAM_PUBLISHED")

    def test_finalize_requires_under_review(self):
        exam2, _, _ = make_exam_with_candidates(self.setup, n=1, exam_code="EX2")
        with self.assertRaises(BusinessRuleError):
            ResultPublicationService.finalize(exam2, actor=self.setup["admin"])

    def test_publish_requires_finalized(self):
        exam2, _, _ = make_exam_with_candidates(self.setup, n=1, exam_code="EX3")
        with self.assertRaises(BusinessRuleError):
            ResultPublicationService.publish(exam2)


class CorrectionWorkflowTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.exam, self.candidates, self.subjects, self.component = _setup_published(self.setup)

    def test_correction_full_flow(self):
        correction = ResultCorrectionService.create_request(
            self.exam, self.candidates[0], self.component,
            requested_value=Decimal("95"), reason="Marking error",
            actor=self.setup["admin"],
        )
        self.assertEqual(correction.old_value, Decimal("50"))
        with self.assertRaises(BusinessRuleError):
            ResultCorrectionService.apply(correction, actor=self.setup["admin"])
        ResultCorrectionService.review(correction, self.setup["admin"], approve=True)
        ResultCorrectionService.apply(correction, self.setup["admin"])

        correction.refresh_from_db()
        self.assertEqual(correction.status, ResultCorrectionRequest.Status.APPLIED)
        mark = Mark.objects.get(exam_candidate=self.candidates[0])
        self.assertEqual(mark.value, Decimal("95"))
        result = CandidateSubjectResult.objects.get(exam_candidate=self.candidates[0])
        self.assertEqual(result.percentage, Decimal("95.00"))
        kinds = list(
            ResultSnapshot.objects.filter(examination=self.exam)
            .order_by("created_at").values_list("kind", flat=True)
        )
        self.assertEqual(kinds, ["FINALIZED", "PUBLISHED", "CORRECTED"])
        log = MarkChangeLog.objects.filter(exam_candidate=self.candidates[0]).latest("created_at")
        self.assertEqual(log.new_value, Decimal("95"))

    def test_correction_on_unpublished_rejected(self):
        exam2, candidates2, subjects2 = make_exam_with_candidates(
            self.setup, n=1, subjects={"ENG": {}}, exam_code="EX2"
        )
        with self.assertRaises(BusinessRuleError):
            ResultCorrectionService.create_request(
                exam2, candidates2[0], subjects2["ENG"].components.first(),
                requested_value=Decimal("10"), reason="x",
            )

    def test_rejected_cannot_apply(self):
        correction = ResultCorrectionService.create_request(
            self.exam, self.candidates[0], self.component,
            requested_value=Decimal("95"), reason="x", actor=self.setup["admin"],
        )
        ResultCorrectionService.review(correction, self.setup["admin"], approve=False)
        with self.assertRaises(BusinessRuleError):
            ResultCorrectionService.apply(correction, self.setup["admin"])


class FakeProvider(BaseSMSProvider):
    name = "fake"
    sent = []

    def send_sms(self, phone, message):
        FakeProvider.sent.append((phone, message))
        return True, f"fake-{phone}", ""


class SMSTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.exam, self.candidates, self.subjects, self.component = _setup_published(self.setup)
        FakeProvider.sent = []

    def _process(self):
        with patch("apps.sms.services.get_provider", return_value=FakeProvider()):
            pass
        # process_campaign resolves provider at call time
        with patch("apps.sms.services.get_provider", return_value=FakeProvider()):
            return None

    def test_campaign_requires_published(self):
        exam2, _, _ = make_exam_with_candidates(self.setup, n=1, exam_code="EX2")
        with self.assertRaises(BusinessRuleError):
            SMSService.queue_campaign(exam2)

    def test_template_rejects_bad_placeholders(self):
        template = SMSTemplate(
            school=self.setup["school"], name="Bad",
            body="Hello {password}",
        )
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            template.clean()

    def test_campaign_queues_messages_and_processes(self):
        campaign, created = SMSService.queue_campaign(self.exam, actor=self.setup["admin"])
        self.assertTrue(created)
        self.assertEqual(campaign.queued, 3)
        self.assertEqual(SMSMessage.objects.filter(campaign=campaign).count(), 3)
        msg = SMSMessage.objects.first()
        self.assertIn("nafasi", msg.message.lower())

        with patch("apps.sms.services.get_provider", return_value=FakeProvider()):
            SMSService.process_campaign(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, SMSCampaign.Status.COMPLETED)
        self.assertEqual(campaign.sent, 3)
        self.assertEqual(len(FakeProvider.sent), 3)

    def test_duplicate_send_skipped(self):
        SMSService.queue_campaign(self.exam, actor=self.setup["admin"])
        with patch("apps.sms.services.get_provider", return_value=FakeProvider()):
            SMSService.process_campaign(
                SMSCampaign.objects.first().pk
            )
        campaign2, created2 = SMSService.queue_campaign(self.exam, actor=self.setup["admin"])
        self.assertTrue(created2)
        self.assertEqual(campaign2.queued, 0)
        self.assertEqual(campaign2.skipped, 3)

    def test_idempotency_key_dedupes(self):
        c1, created1 = SMSService.queue_campaign(
            self.exam, idempotency_key="run-1", actor=self.setup["admin"]
        )
        c2, created2 = SMSService.queue_campaign(
            self.exam, idempotency_key="run-1", actor=self.setup["admin"]
        )
        self.assertFalse(created2)
        self.assertEqual(c1.pk, c2.pk)

    def test_no_phone_candidates_skipped(self):
        exam, candidates, subjects, component = _setup_published(
            self.setup, n=2, guardian_phone="", exam_code="NP"
        )
        # published exam with no guardian phones
        campaign, _ = SMSService.queue_campaign(exam, actor=self.setup["admin"])
        self.assertEqual(campaign.queued, 0)
        self.assertEqual(campaign.skipped, 2)

    def test_invalid_phone_skipped(self):
        from apps.candidates.models import Candidate

        exam, candidates, subjects, component = _setup_published(
            self.setup, n=1, guardian_phone="not-a-phone", exam_code="IP"
        )
        campaign, _ = SMSService.queue_campaign(exam, actor=self.setup["admin"])
        self.assertEqual(campaign.queued, 0)
        self.assertEqual(campaign.skipped, 1)

    def test_resend_failed(self):
        class FailProvider(BaseSMSProvider):
            name = "fail"

            def send_sms(self, phone, message):
                return False, "", "gateway down"

        campaign, _ = SMSService.queue_campaign(self.exam, actor=self.setup["admin"])
        with patch("apps.sms.services.get_provider", return_value=FailProvider()):
            SMSService.process_campaign(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.failed, 3)

        SMSService.resend_failed(campaign.pk, actor=self.setup["admin"])
        self.assertEqual(
            SMSMessage.objects.filter(campaign=campaign, status=SMSMessage.Status.QUEUED).count(), 3
        )
