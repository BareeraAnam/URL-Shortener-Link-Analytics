from django.conf import settings
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.core.ratelimit import is_limited
from apps.core.utils import get_client_ip

from .cache import get_link_payload
from .middleware import UNLOCK_COOKIE_MAX_AGE, UNLOCK_COOKIE_SALT, unlock_cookie_name
from .models import Link


@require_http_methods(["GET", "POST"])
def unlock_view(request, slug: str):
    """Password gate for protected links. Sets a signed, short-lived cookie."""
    payload = get_link_payload(slug)
    if payload is None or not payload["active"]:
        return render(
            request,
            "links/message.html",
            {"title": "Link not found", "message": "This short link does not exist."},
            status=404,
        )
    if not payload["pw"]:
        return redirect(f"/{slug}")

    error = None
    if request.method == "POST":
        key = lambda group, req: f"unlock:{get_client_ip(req)}:{slug}"  # noqa: E731
        if is_limited(request, "unlock", key, settings.UNLOCK_RATE_LIMIT):
            return render(
                request,
                "links/message.html",
                {"title": "Too many attempts", "message": "Please wait a minute and try again."},
                status=429,
            )
        link = Link.objects.filter(pk=payload["id"]).first()
        if link and link.check_password(request.POST.get("password", "")):
            response = redirect(f"/{slug}")
            response.set_signed_cookie(
                unlock_cookie_name(slug),
                link.password_fingerprint,
                salt=UNLOCK_COOKIE_SALT,
                max_age=UNLOCK_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
                secure=request.is_secure(),
            )
            return response
        error = "Incorrect password."
    return render(request, "links/unlock.html", {"slug": slug, "error": error}, status=401 if error else 200)
