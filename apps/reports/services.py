import logging
import uuid

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.audit.services import log_action
from apps.core.exceptions import BusinessRuleError

from .builders import _BUILDERS, build_report_data
from .models import ReportJob
from .renderers import CONTENT_TYPES, EXTENSIONS, render
from .tabularize import tabularize

logger = logging.getLogger("emas.reports")


class ReportGenerationService:
    """Queue and generate reports. Large jobs run through Celery; all
    formats share the same builders over the central result data."""

    @staticmethod
    def create_job(school, report_type, fmt, examination, params=None, actor=None):
        if report_type not in _BUILDERS:
            raise BusinessRuleError("Unknown report type.", code="VALIDATION_ERROR")
        job = ReportJob.objects.create(
            school=school,
            examination=examination,
            report_type=report_type,
            format=fmt,
            params=params or {},
            requested_by=actor,
        )
        log_action(actor=actor, action="REPORT_QUEUED", entity=job, school=school)
        return job

    @staticmethod
    def generate(job: ReportJob):
        job.status = ReportJob.Status.PROCESSING
        job.save(update_fields=["status", "updated_at"])
        try:
            data = build_report_data(job.examination, job.report_type, job.params)
            doc = tabularize(data, job.report_type)
            payload = render(doc, job.format)
            ext = EXTENSIONS[job.format]
            job.file.save(
                f"{job.report_type.lower()}_{job.examination.code}_{uuid.uuid4().hex[:8]}.{ext}",
                ContentFile(payload),
                save=False,
            )
            job.status = ReportJob.Status.COMPLETED
            job.completed_at = timezone.now()
            job.save(update_fields=["file", "status", "completed_at", "updated_at"])
            log_action(actor=None, action="REPORT_COMPLETED", entity=job, school=job.school)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Report job %s failed", job.pk)
            job.status = ReportJob.Status.FAILED
            job.error = str(exc)[:2000]
            job.save(update_fields=["status", "error", "updated_at"])
            log_action(actor=None, action="REPORT_FAILED", entity=job, school=job.school)
            raise
        return job
