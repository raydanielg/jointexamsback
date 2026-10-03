import logging

from celery import shared_task

logger = logging.getLogger("emas.exports.tasks")


@shared_task(bind=True, max_retries=1)
def run_export_task(self, job_id):
    from .models import ExportJob
    from .services import ExportService

    job = ExportJob.objects.get(pk=job_id)
    try:
        ExportService.generate(job)
        return {"job_id": job_id, "status": job.status}
    except Exception as exc:
        logger.exception("Export %s failed", job_id)
        raise self.retry(exc=exc, countdown=10)
