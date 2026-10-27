"""Read-side analytics: aggregation queries that power the dashboard."""

from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.links.models import Link

from .models import Click

DIMENSIONS = {
    "browsers": "browser",
    "operating_systems": "os",
    "devices": "device_type",
    "countries": "country_name",
    "referrers": "referrer_domain",
}


def _breakdown(qs, field: str, limit: int = 10) -> list[dict]:
    rows = qs.order_by().values(field).annotate(clicks=Count("id")).order_by("-clicks", field)[:limit]
    return [{"label": r[field] or "Unknown", "clicks": r["clicks"]} for r in rows]


def window_start(days: int):
    """Start of the reporting window: midnight, ``days - 1`` days ago. Used by EVERY number on a
    report so totals, the daily series and the top-links table always add up."""
    now = timezone.localtime()
    return (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)


def build_report(clicks, days: int, include_bots: bool) -> dict:
    now = timezone.localtime()
    start = window_start(days)
    qs = clicks.filter(clicked_at__gte=start)
    if not include_bots:
        qs = qs.filter(is_bot=False)

    # ip_hash is salted per day (privacy), so this is "unique visitors per day, summed over the
    # period": the same person on 3 different days counts 3 times. Per-day values are exact.
    totals = qs.order_by().aggregate(total_clicks=Count("id"), daily_unique_visitors=Count("ip_hash", distinct=True))
    rows = {
        r["day"]: r
        for r in qs.annotate(day=TruncDate("clicked_at"))
        .order_by()
        .values("day")
        .annotate(clicks=Count("id"), unique_visitors=Count("ip_hash", distinct=True))
    }
    timeseries = []
    for i in range(days):  # zero-fill so charts have a continuous x-axis
        day = start.date() + timedelta(days=i)
        row = rows.get(day)
        timeseries.append(
            {
                "date": day.isoformat(),
                "clicks": row["clicks"] if row else 0,
                "unique_visitors": row["unique_visitors"] if row else 0,
            }
        )
    report = {
        "period": {"days": days, "start": start.date().isoformat(), "end": now.date().isoformat()},
        **totals,
        "timeseries": timeseries,
    }
    for key, field in DIMENSIONS.items():
        report[key] = _breakdown(qs, field)
    return report


def _cached(key: str, builder):
    ttl = settings.ANALYTICS_CACHE_TTL
    if ttl <= 0:
        return builder()
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, ttl)
    return data


def link_report(link: Link, days: int, include_bots: bool) -> dict:
    def build():
        report = build_report(Click.objects.filter(link=link), days, include_bots)
        report["link"] = {"id": link.pk, "slug": link.slug, "short_url": link.short_url, "title": link.title}
        return report

    return _cached(f"analytics:link:{link.pk}:{days}:{int(include_bots)}", build)


def overview_report(user, days: int, include_bots: bool) -> dict:
    def build():
        clicks = Click.objects.filter(link__owner=user)
        report = build_report(clicks, days, include_bots)
        scoped = clicks.filter(clicked_at__gte=window_start(days))
        if not include_bots:
            scoped = scoped.filter(is_bot=False)
        top = (
            scoped.order_by()
            .values("link_id", "link__slug", "link__title")
            .annotate(clicks=Count("id"))
            .order_by("-clicks")[:5]
        )
        report["top_links"] = [
            {"id": r["link_id"], "slug": r["link__slug"], "title": r["link__title"], "clicks": r["clicks"]} for r in top
        ]
        now = timezone.now()
        links = Link.objects.filter(owner=user)
        report["links"] = {
            "total": links.count(),
            "active": links.filter(is_active=True).exclude(expires_at__lte=now).count(),
        }
        return report

    return _cached(f"analytics:overview:{user.pk}:{days}:{int(include_bots)}", build)
