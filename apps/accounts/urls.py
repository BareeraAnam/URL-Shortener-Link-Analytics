from django.contrib.auth import views as auth_views
from django.urls import path

from .page_views import ThrottledLoginView, register_view

urlpatterns = [
    path("login/", ThrottledLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", register_view, name="register"),
]
