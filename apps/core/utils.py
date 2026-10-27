"""Small shared helpers: client IP detection and rate-limit key functions."""

from django.conf import settings
from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    """Return the real client IP.

    ``NUM_PROXIES`` is the number of trusted proxies appending to X-Forwarded-For.
    The client is the Nth entry from the right; anything left of it is attacker
    controlled and must never be trusted.
    """
    proxies = settings.NUM_PROXIES
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if proxies and forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= proxies:
            return parts[-proxies]
    return request.META.get("REMOTE_ADDR", "")


def ip_key(group: str, request) -> str:
    return f"ip:{get_client_ip(request)}"


def user_or_ip_key(group: str, request) -> str:
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return f"user:{user.pk}"
    return ip_key(group, request)
