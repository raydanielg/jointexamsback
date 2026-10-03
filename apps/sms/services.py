import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.candidates.validators import normalize_phone, validate_phone_number
from apps.core.exceptions import BusinessRuleError
from apps.enrollment.models import ExaminationCandidate
from apps.examinations.models import Examination
from apps.results.models import CandidateExamResult, ResultStatus

from .models import DEFAULT_SMS_BODY, SMSCampaign, SMSMessage, SMSTemplate
from .providers import get_provider

logger = logging.getLogger("emas.sms")


class SMSService:
    """Result notification queue. All sends are async via Celery."""

    @staticmethod
    def _template_for(examination, template_id=None):
        """Persisted template or None (caller falls back to DEFAULT_SMS_BODY)."""
        if template_id:
            return SMSTemplate.objects.get(pk=template_id)
        if examination.sms_template_id:
            return examination.sms_template
        return SMSTemplate.objects.filter(
            examination=examination
        ).first() or SMSTemplate.objects.filter(
            school=examination.school, is_default=True
        ).first()

    @staticmethod
    def _context_for(exam, result):
        ec = result.exam_candidate
        return {
            "candidate_name": ec.candidate.full_name,
            "candidate_number": ec.candidate_number,
            "position": result.position or "",
            "school_position": result.school_position or "",
            "exam_name": exam.name,
            "school_name": ec.school.school_name,
            "average": str(result.average_percentage or ""),
            "grade": result.grade,
            "division": result.division,
            "total": str(result.total_score or ""),
        }

    @staticmethod
    def preview_count(examination, candidate_ids=None):
        results = CandidateExamResult.objects.filter(
            examination=examination,
            status=ResultStatus.SCORED,
        ).select_related("exam_candidate__candidate", "exam_candidate__school")
        if candidate_ids:
            results = results.filter(exam_candidate_id__in=candidate_ids)
        valid = skipped = 0
        for r in results.iterator():
            phone = r.exam_candidate.candidate.guardian_phone or r.exam_candidate.candidate.phone
            try:
                validate_phone_number(phone)
                valid += 1
            except Exception:
                skipped += 1
        return {"candidates": results.count(), "valid_contacts": valid, "no_phone": skipped}

    @staticmethod
    @transaction.atomic
    def queue_campaign(examination: Examination, *, candidate_ids=None,
                       template_id=None, resend_failed=False,
                       idempotency_key="", actor=None):
        exam = Examination.objects.select_for_update().get(pk=examination.pk)
        if exam.status != Examination.Status.PUBLISHED:
            raise BusinessRuleError(
                "SMS can only be sent for published results.", code="NOT_PUBLISHED"
            )
        if idempotency_key:
            existing = SMSCampaign.objects.filter(
                examination=exam, idempotency_key=idempotency_key
            ).first()
            if existing:
                return existing, False

        template = SMSService._template_for(exam, template_id)
        renderer = template or SMSTemplate(name="default", body=DEFAULT_SMS_BODY)
        renderer.clean()

        campaign = SMSCampaign.objects.create(
            examination=exam,
            school=exam.school,
            template=template,
            idempotency_key=idempotency_key,
            scope={"candidates": [str(c) for c in candidate_ids] if candidate_ids else []},
            created_by=actor,
        )

        results_qs = CandidateExamResult.objects.filter(
            examination=exam, status=ResultStatus.SCORED
        ).select_related("exam_candidate__candidate", "exam_candidate__school")
        if candidate_ids:
            results_qs = results_qs.filter(exam_candidate_id__in=candidate_ids)

        # Candidates that already received an SMS for this exam (any
        # completed campaign) are skipped unless resend_failed is set.
        already_sent = set()
        if not resend_failed:
            already_sent = set(
                SMSMessage.objects.filter(
                    campaign__examination=exam, status=SMSMessage.Status.SENT
                ).values_list("exam_candidate_id", flat=True)
            )

        messages = []
        skipped = 0
        for result in results_qs.iterator(chunk_size=500):
            ec = result.exam_candidate
            if ec.pk in already_sent:
                skipped += 1
                continue
            candidate = ec.candidate
            phone = candidate.guardian_phone or candidate.phone
            normalized = normalize_phone(phone)
            try:
                validate_phone_number(normalized)
            except Exception:
                skipped += 1
                continue
            messages.append(
                SMSMessage(
                    campaign=campaign,
                    exam_candidate=ec,
                    phone=normalized,
                    message=renderer.render(SMSService._context_for(exam, result)),
                )
            )
        SMSMessage.objects.bulk_create(messages, batch_size=500)
        campaign.total = len(messages) + skipped
        campaign.queued = len(messages)
        campaign.skipped = skipped
        campaign.save(update_fields=["total", "queued", "skipped", "updated_at"])

        log_action(
            actor=actor, action="SMS_CAMPAIGN_QUEUED", entity=campaign, school=exam.school,
            metadata={"queued": len(messages), "skipped": skipped},
        )
        return campaign, True

    @staticmethod
    def dispatch(campaign_id):
        from .tasks import send_campaign_task

        send_campaign_task.delay(str(campaign_id))

    @staticmethod
    @transaction.atomic
    def process_campaign(campaign_id):
        campaign = SMSCampaign.objects.select_for_update().get(pk=campaign_id)
        provider = get_provider()
        campaign.status = SMSCampaign.Status.PROCESSING
        campaign.save(update_fields=["status", "updated_at"])
        sent = failed = 0
        pending = campaign.messages.filter(status=SMSMessage.Status.QUEUED).iterator(chunk_size=200)
        for message in pending:
            message.status = SMSMessage.Status.SENDING
            message.attempts += 1
            try:
                ok_flag, provider_id, error = provider.send_sms(message.phone, message.message)
            except Exception as exc:  # noqa: BLE001
                ok_flag, provider_id, error = False, "", str(exc)[:500]
            if ok_flag:
                message.status = SMSMessage.Status.SENT
                message.provider_message_id = provider_id or ""
                message.provider = provider.name
                message.sent_at = timezone.now()
                sent += 1
            else:
                message.status = SMSMessage.Status.FAILED
                message.failure_reason = error[:1000]
                failed += 1
            message.save(
                update_fields=[
                    "status", "provider_message_id", "provider", "sent_at",
                    "failure_reason", "attempts", "updated_at",
                ]
            )
        campaign.sent = sent
        campaign.failed = failed
        campaign.status = SMSCampaign.Status.COMPLETED
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=["sent", "failed", "status", "completed_at", "updated_at"])
        log_action(
            actor=None, action="SMS_CAMPAIGN_DONE", entity=campaign, school=campaign.school,
            metadata={"sent": sent, "failed": failed},
        )
        return campaign

    @staticmethod
    @transaction.atomic
    def resend_failed(campaign_id, actor=None):
        campaign = SMSCampaign.objects.select_for_update().get(pk=campaign_id)
        failed = campaign.messages.filter(status=SMSMessage.Status.FAILED)
        count = failed.update(status=SMSMessage.Status.QUEUED, failure_reason="")
        if count:
            campaign.status = SMSCampaign.Status.QUEUED
            campaign.save(update_fields=["status", "updated_at"])
        log_action(
            actor=actor, action="SMS_RESEND_FAILED", entity=campaign,
            school=campaign.school, metadata={"requeued": count},
        )
        return campaign

    @staticmethod
    def summary(campaign: SMSCampaign):
        from django.db.models import Count

        counts = dict(
            campaign.messages.values_list("status").annotate(n=Count("pk"))
            .values_list("status", "n")
        )
        return {
            "campaign": str(campaign.pk),
            "examination": campaign.examination.code,
            "status": campaign.status,
            "total": campaign.total,
            "queued": campaign.queued,
            "sent": campaign.sent,
            "failed": campaign.failed,
            "skipped_no_phone": campaign.skipped,
            "by_status": counts,
        }
