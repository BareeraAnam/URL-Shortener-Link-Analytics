import hashlib

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.models import TimeStampedModel

from .validators import validate_destination_url, validate_slug_format, validate_slug_not_reserved

# Fields that are NOT part of the cached redirect payload, so updating them never needs invalidation.
# (The async click worker updates only these, once per click - it must stay cheap.)
CACHE_SAFE_FIELDS = frozenset({"click_count", "last_clicked_at", "updated_at"})


class LinkQuerySet(models.QuerySet):
    """QuerySet.update()/bulk_update() bypass signals, so they must invalidate the redirect cache themselves.

    Without this, ``Link.objects.filter(...).update(is_active=False)`` (e.g. disabling a phishing
    link from an admin action) would keep redirecting for up to LINK_CACHE_TTL.
    """

    def update(self, **kwargs):
        if CACHE_SAFE_FIELDS.issuperset(kwargs):
            return super().update(**kwargs)
        from .cache import invalidate

        slugs = set(self.values_list("slug", flat=True))
        if isinstance(kwargs.get("slug"), str):
            slugs.add(kwargs["slug"])
        rows = super().update(**kwargs)
        for slug in slugs:
            invalidate(slug)
        transaction.on_commit(lambda: [invalidate(slug) for slug in slugs])
        return rows

    def bulk_update(self, objs, fields, batch_size=None):
        from .cache import invalidate

        objs = list(objs)
        rows = super().bulk_update(objs, fields, batch_size)
        if not CACHE_SAFE_FIELDS.issuperset(fields):
            slugs = {o.slug for o in objs}
            for slug in slugs:
                invalidate(slug)
            transaction.on_commit(lambda: [invalidate(slug) for slug in slugs])
        return rows


class Link(TimeStampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="links")
    slug = models.CharField(max_length=32, unique=True, validators=[validate_slug_format, validate_slug_not_reserved])
    original_url = models.URLField(max_length=2048, validators=[validate_destination_url])
    title = models.CharField(max_length=200, blank=True)
    password = models.CharField(max_length=128, blank=True, help_text="Hashed; empty = public link")
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True)
    click_count = models.PositiveBigIntegerField(default=0)
    last_clicked_at = models.DateTimeField(null=True, blank=True)

    objects = LinkQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "-created_at"])]
        constraints = [
            # /Promo and /promo must never be two different links (look-alike / phishing confusion).
            models.UniqueConstraint(Lower("slug"), name="link_slug_ci_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.slug} -> {self.original_url}"

    # -- password protection
    def set_password(self, raw: str | None, hash_cache: dict | None = None) -> None:
        """Hash ``raw``. PBKDF2 costs ~0.5-1 s, so bulk imports pass a per-job ``hash_cache``
        to hash each distinct password only once."""
        if not raw:
            self.password = ""
        elif hash_cache is None:
            self.password = make_password(raw)
        else:
            if raw not in hash_cache:
                hash_cache[raw] = make_password(raw)
            self.password = hash_cache[raw]

    def check_password(self, raw: str) -> bool:
        return bool(self.password) and check_password(raw, self.password)

    @property
    def has_password(self) -> bool:
        return bool(self.password)

    @property
    def password_fingerprint(self) -> str:
        """Changes whenever the password changes -> invalidates old unlock cookies."""
        if not self.password:
            return ""
        return hashlib.sha256(self.password.encode()).hexdigest()[:16]

    # -- state
    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def short_url(self) -> str:
        return f"{settings.SHORT_BASE_URL.rstrip('/')}/{self.slug}"


class BulkImportJob(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        PROCESSING = "processing"
        COMPLETED = "completed"
        FAILED = "failed"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="import_jobs")
    file_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    total_rows = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    payload = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Import #{self.pk} ({self.status})"
