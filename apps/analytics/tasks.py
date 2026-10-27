from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import OperationalError
from django.utils import timezone

from .models import Click
from .tracking import store_click


@shared_task(
    name="analytics.record_click",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=5,
    ignore_result=True,
)
def record_click(link_id, ip, user_agent, referrer, clicked_at, country_hint="", click_id=""):
    """Async analytics write so the redirect response never waits on the database."""
    store_click(link_id, ip, user_agent, referrer, clicked_at, country_hint, click_id)


@shared_task(name="analytics.purge_old_clicks", ignore_result=True)
def purge_old_clicks(batch_size: int = 5000) -> int:
    """Retention policy (GDPR-friendly): delete clicks older than CLICK_RETENTION_DAYS in batches."""
    cutoff = timezone.now() - timedelta(days=settings.CLICK_RETENTION_DAYS)
    total = 0
    while True:
        ids = list(Click.objects.filter(clicked_at__lt=cutoff).values_list("pk", flat=True)[:batch_size])
        if not ids:
            return total
        Click.objects.filter(pk__in=ids).delete()
        total += len(ids)
