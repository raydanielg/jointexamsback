import logging

from celery import shared_task

logger = logging.getLogger("emas.reports.tasks")


@shared_task(bind=True, max_retries=1)
def generate_report_task(self, job_id):
    from .models import ReportJob
    from .services import ReportGenerationService

    job = ReportJob.objects.select_related("examination").get(pk=job_id)
    try:
        ReportGenerationService.generate(job)
        return {"job_id": job_id, "status": job.status}
    except Exception as exc:
        logger.exception("Report generation failed for %s", job_id)
        raise self.retry(exc=exc, countdown=15)
