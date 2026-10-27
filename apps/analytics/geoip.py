"""Country lookup using a local MaxMind GeoLite2 database (no network call per click)."""

import logging
from functools import lru_cache
from pathlib import Path

import geoip2.database
import geoip2.errors
from django.conf import settings

logger = logging.getLogger(__name__)
UNKNOWN = ("", "Unknown")


@lru_cache(maxsize=1)
def _reader():
    path = settings.GEOIP_COUNTRY_DB
    if not path or not Path(path).exists():
        logger.warning("GeoIP database not found (%s); countries will be 'Unknown'.", path)
        return None
    return geoip2.database.Reader(path)


def lookup_country(ip: str) -> tuple[str, str]:
    """Return (ISO code, name) or ("", "Unknown")."""
    reader = _reader()
    if reader is None or not ip:
        return UNKNOWN
    try:
        country = reader.country(ip).country
    except (geoip2.errors.AddressNotFoundError, ValueError):
        return UNKNOWN
    return (country.iso_code or "", country.name or "Unknown")
