from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.links.models import Link

from .models import Click
from .serializers import AnalyticsQuerySerializer, ClickSerializer
from .services import link_report, overview_report


def _params(request) -> dict:
    query = AnalyticsQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    return query.validated_data


class LinkAnalyticsView(APIView):
    """Click-through analytics for one link: totals, daily series, browsers, OS, countries, referrers."""

    def get(self, request, pk: int):
        link = get_object_or_404(Link, pk=pk, owner=request.user)
        p = _params(request)
        return Response(link_report(link, p["days"], p["include_bots"]))


class OverviewAnalyticsView(APIView):
    """Account-wide analytics across all of the user's links."""

    def get(self, request):
        p = _params(request)
        return Response(overview_report(request.user, p["days"], p["include_bots"]))


class ClickListView(generics.ListAPIView):
    """Raw, paginated click log for one link (newest first)."""

    serializer_class = ClickSerializer

    def get_queryset(self):
        link = get_object_or_404(Link, pk=self.kwargs["pk"], owner=self.request.user)
        return Click.objects.filter(link=link)
