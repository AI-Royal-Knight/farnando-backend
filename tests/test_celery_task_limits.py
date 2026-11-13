from django.conf import settings

from config.celery import app


def test_every_task_soft_time_limit_fits_inside_its_hard_limit() -> None:
    """Celery refuses to enqueue a task whose soft limit exceeds its hard limit.

    A task that sets only ``time_limit`` inherits ``CELERY_TASK_SOFT_TIME_LIMIT``, so a short
    hard limit silently breaks every ``.delay()`` call for that task.
    """
    app.loader.import_default_modules()
    offenders = []
    for name, task in app.tasks.items():
        if name.startswith("celery."):
            continue
        hard = task.time_limit if task.time_limit is not None else settings.CELERY_TASK_TIME_LIMIT
        soft = (
            task.soft_time_limit
            if task.soft_time_limit is not None
            else settings.CELERY_TASK_SOFT_TIME_LIMIT
        )
        if hard is not None and soft is not None and soft > hard:
            offenders.append(f"{name}: soft={soft} hard={hard}")
    assert offenders == []
