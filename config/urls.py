from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.views import health_check
from apps.links.views_public import unlock_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.links.api_urls")),
    path("api/", include("apps.analytics.urls")),
    path("api/auth/", include("apps.accounts.api_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("health/", health_check, name="health"),
    path("unlock/<str:slug>/", unlock_view, name="unlock"),
    path("", include("apps.accounts.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
]
