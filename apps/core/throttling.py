"""DRF throttles backed by django-ratelimit (shared Redis counters across workers)."""

from django.conf import settings
from rest_framework.throttling import BaseThrottle

from .ratelimit import is_limited
from .utils import ip_key, user_or_ip_key


class DjangoRatelimitThrottle(BaseThrottle):
    group = "api"
    rate_setting = "API_RATE_LIMIT"
    key = staticmethod(user_or_ip_key)

    def allow_request(self, request, view):
        rate = getattr(settings, self.rate_setting)
        return not is_limited(request, self.group, self.key, rate)


class GlobalApiThrottle(DjangoRatelimitThrottle):
    group = "api"
    rate_setting = "API_RATE_LIMIT"


class LinkCreateThrottle(DjangoRatelimitThrottle):
    group = "link-create"
    rate_setting = "LINK_CREATE_RATE_LIMIT"


class BulkImportThrottle(DjangoRatelimitThrottle):
    group = "bulk-import"
    rate_setting = "BULK_IMPORT_RATE_LIMIT"


class AuthThrottle(DjangoRatelimitThrottle):
    group = "auth"
    rate_setting = "AUTH_RATE_LIMIT"
    key = staticmethod(ip_key)
