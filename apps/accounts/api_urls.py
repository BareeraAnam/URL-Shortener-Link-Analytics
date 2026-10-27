from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="api-register"),
    path("token/", views.ThrottledTokenObtainPairView.as_view(), name="token"),
    path("token/refresh/", views.ThrottledTokenRefreshView.as_view(), name="token-refresh"),
    path("me/", views.MeView.as_view(), name="api-me"),
]
