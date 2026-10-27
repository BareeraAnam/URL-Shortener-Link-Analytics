from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .bulk import run_import
from .models import BulkImportJob


@shared_task(
    name="links.process_bulk_import",
    ignore_result=True,
    soft_time_limit=settings.BULK_IMPORT_SOFT_TIME_LIMIT,
    time_limit=settings.BULK_IMPORT_TIME_LIMIT,
)
def process_bulk_import(job_id: int) -> None:
    try:
        job = BulkImportJob.objects.select_related("owner").get(pk=job_id)
    except BulkImportJob.DoesNotExist:
        return
    if job.status != BulkImportJob.Status.PENDING:  # a redelivered message must not re-run a job
        return
    run_import(job)


@shared_task(name="links.fail_stuck_import_jobs", ignore_result=True)
def fail_stuck_import_jobs() -> int:
    """Recovery for jobs whose worker died (OOM, hard time-limit kill, restart).

    Without this a job stays PROCESSING forever, because process_bulk_import only accepts PENDING.
    """
    now = timezone.now()
    stuck = BulkImportJob.objects.filter(
        status=BulkImportJob.Status.PROCESSING,
        updated_at__lt=now - timedelta(minutes=settings.BULK_IMPORT_STUCK_MINUTES),
    ) | BulkImportJob.objects.filter(
        status=BulkImportJob.Status.PENDING,
        created_at__lt=now - timedelta(minutes=settings.BULK_IMPORT_PENDING_MINUTES),
    )
    count = 0
    for job in stuck:
        job.status = BulkImportJob.Status.FAILED
        job.finished_at = now
        job.payload = ""
        job.errors = [
            *job.errors,
            {
                "row": 0,
                "errors": {
                    "non_field_errors": [
                        "The import was interrupted (worker stopped or never started). "
                        f"{job.success_count} links were created. Upload the remaining rows as a new file."
                    ]
                },
            },
        ]
        job.save()
        count += 1
    return count


@shared_task(name="links.purge_old_import_jobs", ignore_result=True)
def purge_old_import_jobs(days: int = 30) -> int:
    deleted, _ = BulkImportJob.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted
