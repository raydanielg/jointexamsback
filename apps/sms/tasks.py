import logging

from celery import shared_task

logger = logging.getLogger("emas.sms.tasks")


@shared_task(bind=True, max_retries=2, acks_late=True)
def send_campaign_task(self, campaign_id):
    from .models import SMSCampaign
    from .services import SMSService

    try:
        campaign = SMSService.process_campaign(campaign_id)
        return {"campaign": campaign_id, "sent": campaign.sent, "failed": campaign.failed}
    except SMSCampaign.DoesNotExist:
        logger.error("SMS campaign %s not found", campaign_id)
        return None
    except Exception as exc:
        logger.exception("SMS campaign %s failed", campaign_id)
        SMSCampaign.objects.filter(pk=campaign_id).update(status="FAILED", error=str(exc)[:2000])
        raise self.retry(exc=exc, countdown=30)
