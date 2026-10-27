"""Thin, failure-tolerant wrapper around django-ratelimit."""

import logging

from django_ratelimit.core import is_ratelimited

logger = logging.getLogger(__name__)


def is_limited(request, group: str, key, rate: str) -> bool:
    """True when the caller exceeded ``rate``. Fails *open* if the cache is down."""
    try:
        return bool(is_ratelimited(request=request, group=group, key=key, rate=rate, increment=True))
    except Exception:  # noqa: BLE001 - availability beats strictness here
        logger.exception("Rate limiter unavailable, allowing request")
        return False
