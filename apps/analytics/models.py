from django.db import models


class Click(models.Model):
    """One redirect event. Raw IPs are never stored - only a salted daily hash."""

    link = models.ForeignKey("links.Link", on_delete=models.CASCADE, related_name="clicks")
    # Generated once per redirect. The unique constraint makes the worker exactly-once even when
    # a message is redelivered (acks_late + worker crash after commit).
    click_id = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    clicked_at = models.DateTimeField(db_index=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    browser = models.CharField(max_length=60, blank=True)
    browser_version = models.CharField(max_length=30, blank=True)
    os = models.CharField(max_length=60, blank=True)
    os_version = models.CharField(max_length=30, blank=True)
    device_type = models.CharField(max_length=12, blank=True)
    is_bot = models.BooleanField(default=False)
    country_code = models.CharField(max_length=2, blank=True)
    country_name = models.CharField(max_length=80, blank=True)
    referrer_domain = models.CharField(max_length=255, blank=True)
    referrer = models.CharField(max_length=512, blank=True)

    class Meta:
        ordering = ["-clicked_at"]
        indexes = [models.Index(fields=["link", "-clicked_at"], name="click_link_time_idx")]

    def __str__(self) -> str:
        return f"Click({self.link_id} @ {self.clicked_at:%Y-%m-%d %H:%M})"
