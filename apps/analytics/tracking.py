"""Turns raw request data into a stored Click (runs inside the Celery worker)."""

import hashlib
import uuid
from datetime import datetime
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.links.models import Link

from .geoip import lookup_country
from .models import Click
from .useragent import parse_user_agent


def parse_referrer(referrer: str) -> str:
    if not referrer:
        return "direct"
    host = (urlparse(referrer).hostname or "").lower()
    return host.removeprefix("www.")[:255] or "direct"


def visitor_hash(ip: str, user_agent: str, when: datetime) -> str:
    """Salted per-day hash: enough for unique-visitor counts, useless for tracking a person."""
    raw = f"{settings.SECRET_KEY}:{when:%Y-%m-%d}:{ip}:{user_agent}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _parse_click_id(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def store_click(link_id, ip, user_agent, referrer, clicked_at, country_hint="", click_id="") -> Click | None:
    """Store one click exactly once.

    The link counter and the Click row are written in ONE transaction, and ``click_id`` is unique.
    If the broker redelivers the message (worker died after commit, before ack) the insert violates
    the constraint, the whole transaction - including the counter increment - is rolled back and the
    duplicate is dropped.
    """
    when = datetime.fromisoformat(clicked_at) if isinstance(clicked_at, str) else clicked_at
    if timezone.is_naive(when):
        when = timezone.make_aware(when)

    ua = parse_user_agent(user_agent)
    code, name = lookup_country(ip)
    hint = (country_hint or "").upper()
    if not code and len(hint) == 2 and hint.isalpha() and hint not in {"XX", "T1"}:
        code, name = hint, hint  # e.g. Cloudflare's CF-IPCountry header

    links = Link.objects.filter(pk=link_id)
    try:
        with transaction.atomic():
            if ua.is_bot:  # recorded for completeness, but not counted as a human click
                if not links.exists():
                    return None
            elif not links.update(click_count=F("click_count") + 1, last_clicked_at=when):
                return None  # link was deleted while the task was queued
            return Click.objects.create(
                link_id=link_id,
                click_id=_parse_click_id(click_id),
                clicked_at=when,
                ip_hash=visitor_hash(ip, user_agent, when),
                browser=ua.browser,
                browser_version=ua.browser_version,
                os=ua.os,
                os_version=ua.os_version,
                device_type=ua.device_type,
                is_bot=ua.is_bot,
                country_code=code,
                country_name=name,
                referrer_domain=parse_referrer(referrer),
                referrer=(referrer or "")[:512],
            )
    except IntegrityError:
        if click_id and Click.objects.filter(click_id=_parse_click_id(click_id)).exists():
            return None  # duplicate delivery - already counted
        raise
