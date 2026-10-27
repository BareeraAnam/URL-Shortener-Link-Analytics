"""Redirect middleware - the performance-critical heart of the project.

It intercepts ``GET /<slug>`` *before* sessions, auth and CSRF run, resolves the
slug from Redis (0 DB queries on a hit), applies expiry / password rules and
returns the redirect. Analytics are handed to Celery so the user never waits.
"""

import logging
import re
import uuid

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone

from apps.analytics.tasks import record_click
from apps.core.ratelimit import is_limited
from apps.core.utils import get_client_ip, ip_key

from .cache import get_link_payload
from .validators import RESERVED_SLUGS

logger = logging.getLogger(__name__)

SLUG_PATH_RE = re.compile(r"^/(?P<slug>[A-Za-z0-9_-]{3,32})/?$")
UNLOCK_COOKIE_SALT = "link-unlock"
UNLOCK_COOKIE_MAX_AGE = 3600


def unlock_cookie_name(slug: str) -> str:
    return f"unlock_{slug}"


class RedirectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("GET", "HEAD"):
            match = SLUG_PATH_RE.match(request.path_info)
            if match and match["slug"].lower() not in RESERVED_SLUGS:
                return self._handle(request, match["slug"])
        return self.get_response(request)

    # ------------------------------------------------------------------ internals
    def _handle(self, request, slug: str):
        request.get_host()  # enforce ALLOWED_HOSTS since we run before CommonMiddleware

        if is_limited(request, "redirect", ip_key, settings.REDIRECT_RATE_LIMIT):
            response = HttpResponse("Too many requests. Please slow down.", status=429, content_type="text/plain")
            response["Retry-After"] = "60"
            return response

        payload = get_link_payload(slug)
        if payload is None or not payload["active"]:
            return self._message(request, 404, "Link not found", "This short link does not exist or was disabled.")

        if payload["exp"] and timezone.now().timestamp() >= payload["exp"]:
            return self._message(request, 410, "Link expired", "This short link has expired.")

        if payload["pw"]:
            token = request.get_signed_cookie(
                unlock_cookie_name(slug), default=None, salt=UNLOCK_COOKIE_SALT, max_age=UNLOCK_COOKIE_MAX_AGE
            )
            if token != payload["pw"]:
                response = HttpResponseRedirect(f"/unlock/{slug}/")
                response["Cache-Control"] = "no-store"
                return response

        if request.method == "GET":  # HEAD (link previews, uptime checks) is not a click
            self._track(request, payload["id"])

        response = HttpResponseRedirect(payload["url"])
        response.status_code = settings.REDIRECT_STATUS_CODE
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex"
        return response

    @staticmethod
    def _track(request, link_id: int) -> None:
        """Fire-and-forget. A broken queue must never break a redirect."""
        try:
            record_click.delay(
                link_id,
                get_client_ip(request),
                request.META.get("HTTP_USER_AGENT", "")[:512],
                request.META.get("HTTP_REFERER", "")[:1024],
                timezone.now().isoformat(),
                request.META.get("HTTP_CF_IPCOUNTRY", ""),
                uuid.uuid4().hex,  # click_id: lets the worker drop redelivered duplicates
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not enqueue click for link %s", link_id)

    @staticmethod
    def _message(request, status: int, title: str, message: str):
        return render(request, "links/message.html", {"title": title, "message": message}, status=status)
