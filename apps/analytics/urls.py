from django.urls import path

from . import views

urlpatterns = [
    path("links/<int:pk>/analytics/", views.LinkAnalyticsView.as_view(), name="link-analytics"),
    path("links/<int:pk>/clicks/", views.ClickListView.as_view(), name="link-clicks"),
    path("analytics/overview/", views.OverviewAnalyticsView.as_view(), name="analytics-overview"),
]
