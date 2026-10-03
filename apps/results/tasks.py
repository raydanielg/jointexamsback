import logging

from celery import shared_task

logger = logging.getLogger("emas.results.tasks")


@shared_task(bind=True, max_retries=1)
def calculate_results_task(self, examination_id, actor_id=None):
    from apps.accounts.models import User
    from apps.examinations.models import Examination

    from .services import ResultCalculationService

    exam = Examination.objects.get(pk=examination_id)
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    try:
        return ResultCalculationService.calculate_exam(exam, actor=actor)
    except Exception as exc:
        logger.exception("Result calculation failed for exam %s", examination_id)
        raise self.retry(exc=exc, countdown=15)
