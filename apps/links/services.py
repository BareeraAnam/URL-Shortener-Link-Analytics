import secrets
import string

from django.conf import settings
from django.db.models.functions import Lower

from .validators import RESERVED_SLUGS

ALPHABET = string.ascii_letters + string.digits


def generate_slug(length: int | None = None) -> str:
    """Cryptographically random base62 slug (62^7 ~ 3.5 trillion combinations)."""
    length = length or settings.AUTO_SLUG_LENGTH
    while True:
        slug = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if slug.lower() not in RESERVED_SLUGS and not slug_taken(slug):
            return slug


def slug_taken(slug: str, exclude_pk: int | None = None) -> bool:
    """Case-insensitive availability check (matches the DB's lower(slug) unique index)."""
    from .models import Link

    qs = Link.objects.annotate(_slug_lower=Lower("slug")).filter(_slug_lower=slug.lower())
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()
