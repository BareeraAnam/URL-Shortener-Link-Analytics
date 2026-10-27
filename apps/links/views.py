from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.core.throttling import BulkImportThrottle, GlobalApiThrottle, LinkCreateThrottle

from .bulk import BulkImportError, validate_upload
from .models import BulkImportJob, Link
from .qr import render_qr
from .serializers import (
    BulkImportJobSerializer,
    LinkSerializer,
    QRQuerySerializer,
    SlugCheckSerializer,
)
from .services import slug_taken
from .tasks import process_bulk_import
from .validators import RESERVED_SLUGS, validate_slug_format


class LinkViewSet(viewsets.ModelViewSet):
    """CRUD for the authenticated user's links (+ QR, bulk import, slug check)."""

    serializer_class = LinkSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["slug", "title", "original_url"]
    ordering_fields = ["created_at", "click_count", "expires_at"]

    def get_throttles(self):
        extra = {"create": LinkCreateThrottle, "bulk_import": BulkImportThrottle}.get(self.action)
        return [GlobalApiThrottle()] + ([extra()] if extra else [])

    def get_queryset(self):
        qs = Link.objects.filter(owner=self.request.user)
        state = self.request.query_params.get("status")
        now = timezone.now()
        if state == "active":
            qs = qs.filter(is_active=True).exclude(expires_at__lte=now)
        elif state == "expired":
            qs = qs.filter(expires_at__lte=now)
        elif state == "disabled":
            qs = qs.filter(is_active=False)
        return qs

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["get"])
    def qr(self, request, pk=None):
        """QR code image for the short URL (?type=png|svg&size=1-20&border=0-10&download=1)."""
        link = self.get_object()
        query = QRQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        opts = query.validated_data
        content, content_type = render_qr(link.short_url, opts["type"], opts["size"], opts["border"])
        response = HttpResponse(content, content_type=content_type)
        response["Cache-Control"] = "private, max-age=3600"
        if opts["download"]:
            response["Content-Disposition"] = f'attachment; filename="{link.slug}.{opts["type"]}"'
        return response

    @action(detail=False, methods=["get"], url_path="check-slug")
    def check_slug(self, request):
        """Live availability check for custom slugs."""
        query = SlugCheckSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        slug = query.validated_data["slug"]
        reason = None
        try:
            validate_slug_format(slug)
        except Exception:  # noqa: BLE001
            reason = "invalid"
        else:
            if slug.lower() in RESERVED_SLUGS:
                reason = "reserved"
            elif slug_taken(slug):
                reason = "taken"
        return Response({"slug": slug, "available": reason is None, "reason": reason})

    @action(detail=False, methods=["post"], url_path="bulk-import", parser_classes=[MultiPartParser])
    def bulk_import(self, request):
        """Upload a CSV (original_url, slug, title, expires_at, password, is_active). Processed async."""
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "Attach a CSV as multipart field 'file'."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            text, total = validate_upload(upload)
        except BulkImportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        job = BulkImportJob.objects.create(
            owner=request.user, file_name=upload.name[:255], total_rows=total, payload=text
        )
        try:
            process_bulk_import.delay(job.pk)
        except Exception:  # noqa: BLE001 - broker down
            job.status = BulkImportJob.Status.FAILED
            job.payload = ""
            job.errors = [{"row": 0, "errors": {"non_field_errors": ["Queue unavailable, retry later."]}}]
            job.save()
            return Response(
                {"detail": "Import queue unavailable. Please retry shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        job.refresh_from_db()
        return Response(BulkImportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path=r"bulk-import/(?P<job_id>[0-9]+)", url_name="bulk-status")
    def bulk_status(self, request, job_id=None):
        job = get_object_or_404(BulkImportJob, pk=job_id, owner=request.user)
        return Response(BulkImportJobSerializer(job).data)
