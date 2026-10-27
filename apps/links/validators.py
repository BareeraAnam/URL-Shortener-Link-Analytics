"""Validation rules for slugs and destination URLs (abuse / phishing / loop protection)."""

import ipaddress
import re
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError

SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

# Paths owned by the application itself; a short link may never shadow them.
RESERVED_SLUGS = frozenset(
    {
        "api",
        "admin",
        "dashboard",
        "unlock",
        "static",
        "media",
        "health",
        "login",
        "logout",
        "register",
        "docs",
        "schema",
        "robots",
        "favicon",
        "sitemap",
        "about",
        "help",
        "support",
        "terms",
        "privacy",
        "app",
        "www",
    }
)


def validate_slug_format(value: str) -> None:
    if not SLUG_RE.match(value or ""):
        raise ValidationError("Slug must be 3-32 characters: letters, numbers, hyphen or underscore.")


def validate_slug_not_reserved(value: str) -> None:
    if (value or "").lower() in RESERVED_SLUGS:
        raise ValidationError("This slug is reserved. Please choose another one.")


def _own_hosts() -> set[str]:
    hosts = {(urlparse(settings.SHORT_BASE_URL).hostname or "").lower()}
    hosts.update(h.lower().lstrip(".") for h in settings.ALLOWED_HOSTS if h and h != "*")
    hosts.discard("")
    return hosts


_NUMERIC_HOST_RE = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$", re.IGNORECASE)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".lan", ".intranet")


def _ip_is_blocked(ip) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)  # ::ffff:127.0.0.1 -> 127.0.0.1
    if mapped is not None:
        ip = mapped
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_destination_url(value: str) -> None:
    parsed = urlparse(value or "")
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("Only http:// and https:// URLs are allowed.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValidationError("URL must contain a valid host.")
    if parsed.username or parsed.password:
        raise ValidationError("URLs containing credentials are not allowed.")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise ValidationError("Local/internal addresses are not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is None and _NUMERIC_HOST_RE.match(host):
        # 2130706433, 0x7f000001, 127.1, 017700000001 ... browsers resolve these to 127.0.0.1
        raise ValidationError("Numeric/obfuscated IP hostnames are not allowed. Use a normal domain name.")
    if ip is not None and _ip_is_blocked(ip):
        raise ValidationError("Private or reserved IP addresses are not allowed.")
    blocked = {d.lower().lstrip(".") for d in settings.BLOCKED_DOMAINS} | _own_hosts()
    if any(host == d or host.endswith("." + d) for d in blocked):
        raise ValidationError("This domain cannot be shortened.")
