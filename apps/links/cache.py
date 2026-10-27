"""Redis-backed lookup used on the redirect hot path.

Flow: cache hit -> 0 DB queries. Miss -> 1 indexed query, then cached.
Unknown slugs are negatively cached for a short time so scanners/bots cannot
hammer the database with random slugs.
"""

import time

from django.conf import settings
from django.core.cache import cache

MISSING = {"missing": True}


def cache_key(slug: str) -> str:
    return f"link:v1:{slug}"


def build_payload(link) -> dict:
    return {
        "id": link.pk,
        "url": link.original_url,
        "active": link.is_active,
        "exp": int(link.expires_at.timestamp()) if link.expires_at else None,
        "pw": link.password_fingerprint,  # fingerprint only, never the hash
    }


def _ttl(payload: dict) -> int:
    ttl = settings.LINK_CACHE_TTL
    if payload.get("exp"):
        ttl = min(ttl, max(payload["exp"] - int(time.time()), 1))
    return ttl


def get_link_payload(slug: str) -> dict | None:
    from .models import Link

    key = cache_key(slug)
    cached = cache.get(key)
    if cached is not None:
        return None if cached.get("missing") else cached

    link = Link.objects.filter(slug=slug).first()
    if link is None:
        cache.set(key, MISSING, settings.MISSING_CACHE_TTL)
        return None
    payload = build_payload(link)
    cache.set(key, payload, _ttl(payload))
    return payload


def invalidate(slug: str) -> None:
    cache.delete(cache_key(slug))
