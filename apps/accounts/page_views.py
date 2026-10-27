from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from apps.core.ratelimit import is_limited
from apps.core.utils import ip_key

from .forms import SignUpForm


def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignUpForm(request.POST or None)
    rate_error = None
    if request.method == "POST":
        if is_limited(request, "signup", ip_key, settings.AUTH_RATE_LIMIT):
            rate_error = "Too many attempts. Please try again in a minute."
        elif form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("dashboard")
    return render(
        request,
        "registration/register.html",
        {"form": form, "rate_error": rate_error},
        status=429 if rate_error else 200,
    )


class ThrottledLoginView(LoginView):
    """Login page with brute-force protection: limits attempts per IP *and* per username."""

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "").strip().lower()[:150]
        by_user = lambda group, req: f"login-user:{username}"  # noqa: E731
        if is_limited(request, "login-ip", ip_key, settings.LOGIN_RATE_LIMIT) or is_limited(
            request, "login-user", by_user, settings.LOGIN_RATE_LIMIT
        ):
            form = self.get_form()
            form.add_error(None, "Too many login attempts. Please wait a minute and try again.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)
