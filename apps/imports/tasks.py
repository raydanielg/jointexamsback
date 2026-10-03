import logging

from celery import shared_task

logger = logging.getLogger("emas.imports.tasks")


@shared_task(bind=True, max_retries=1)
def process_import_task(self, session_id):
    from .models import ImportSession
    from .services import ImportService

    session = ImportSession.objects.get(pk=session_id)
    try:
        ImportService.process(session)
        return {"session": session_id, "status": session.status}
    except Exception as exc:
        logger.exception("Import %s failed", session_id)
        raise self.retry(exc=exc, countdown=10)
