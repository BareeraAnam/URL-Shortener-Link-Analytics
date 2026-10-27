import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.links.models import BulkImportJob, Link
from apps.links.tasks import process_bulk_import, purge_old_import_jobs

URL = "/api/links/bulk-import/"


def csv_file(text: str, name="links.csv"):
    return SimpleUploadedFile(name, text.encode(), content_type="text/csv")


@pytest.mark.django_db
class TestBulkImport:
    def test_happy_path(self, api, user):
        data = (
            "original_url,slug,title,expires_at,password\n"
            "https://example.com/1,bulk-one,First,,\n"
            "https://example.com/2,,Second,2999-01-01T00:00:00Z,pw123\n"
            "https://example.com/3,,,,\n"
        )
        r = api.post(URL, {"file": csv_file(data)}, format="multipart")
        assert r.status_code == 202
        job = BulkImportJob.objects.get(pk=r.data["id"])  # Celery eager -> already processed
        assert (job.status, job.success_count, job.failed_count, job.total_rows) == ("completed", 3, 0, 3)
        assert job.payload == "" and job.finished_at
        assert Link.objects.filter(owner=user).count() == 3
        assert Link.objects.get(slug="bulk-one").title == "First"
        second = Link.objects.get(title="Second")
        assert second.has_password and second.expires_at is not None

    def test_partial_failures_reported_per_row(self, api, make_link):
        make_link(slug="exists1")
        data = (
            "url,slug\n"
            "https://example.com/ok,fine-slug\n"
            "not-a-url,\n"
            "https://example.com/dup,exists1\n"
            "https://example.com/res,admin\n"
            "http://localhost/x,\n"
            "https://example.com/again,fine-slug\n"
        )
        r = api.post(URL, {"file": csv_file(data)}, format="multipart")
        job = BulkImportJob.objects.get(pk=r.data["id"])
        assert job.status == "completed" and job.success_count == 1 and job.failed_count == 5
        assert [e["row"] for e in job.errors] == [3, 4, 5, 6, 7]
        assert "slug" in job.errors[1]["errors"] and "original_url" in job.errors[3]["errors"]

    def test_status_endpoint_and_ownership(self, api, other_user):
        r = api.post(URL, {"file": csv_file("original_url\nhttps://example.com/x\n")}, format="multipart")
        status = api.get(f"{URL}{r.data['id']}/")
        assert status.status_code == 200 and status.data["status"] == "completed"
        foreign = BulkImportJob.objects.create(owner=other_user)
        assert api.get(f"{URL}{foreign.id}/").status_code == 404

    @pytest.mark.parametrize(
        "content,expected",
        [("slug,title\nabc,x\n", "original_url"), ("", "empty"), ("original_url\n", "no data rows")],
    )
    def test_bad_files_rejected(self, api, content, expected):
        r = api.post(URL, {"file": csv_file(content)}, format="multipart")
        assert r.status_code == 400 and expected in r.data["detail"].lower() + r.data["detail"]

    def test_missing_file_and_non_utf8(self, api):
        assert api.post(URL, {}, format="multipart").status_code == 400
        bad = SimpleUploadedFile("x.csv", b"\xff\xfe\x00bad", content_type="text/csv")
        assert api.post(URL, {"file": bad}, format="multipart").status_code == 400

    def test_row_and_size_limits(self, api, settings):
        settings.BULK_IMPORT_MAX_ROWS = 2
        rows = "original_url\n" + "\n".join(f"https://example.com/{i}" for i in range(3))
        assert api.post(URL, {"file": csv_file(rows)}, format="multipart").status_code == 400
        settings.BULK_IMPORT_MAX_BYTES = 10
        assert api.post(URL, {"file": csv_file(rows)}, format="multipart").status_code == 400

    def test_bom_and_blank_lines_tolerated(self, api, user):
        data = "\ufefforiginal_url,title\n\nhttps://example.com/a,A\n,,\n"
        r = api.post(URL, {"file": csv_file(data)}, format="multipart")
        assert r.status_code == 202 and Link.objects.filter(owner=user).count() == 1

    def test_requires_auth_and_rate_limited(self, anon_api, api, settings):
        assert anon_api.post(URL, {}, format="multipart").status_code == 401
        settings.BULK_IMPORT_RATE_LIMIT = "1/h"
        good = "original_url\nhttps://example.com/a\n"
        assert api.post(URL, {"file": csv_file(good)}, format="multipart").status_code == 202
        assert api.post(URL, {"file": csv_file(good)}, format="multipart").status_code == 429

    def test_task_is_idempotent_and_ignores_missing_job(self, user):
        job = BulkImportJob.objects.create(owner=user, status="completed")
        process_bulk_import(job.pk)
        process_bulk_import(987654)
        job.refresh_from_db()
        assert job.status == "completed"

    def test_queue_down_returns_503(self, api):
        from unittest.mock import patch

        with patch("apps.links.views.process_bulk_import.delay", side_effect=ConnectionError):
            r = api.post(URL, {"file": csv_file("original_url\nhttps://example.com/a\n")}, format="multipart")
        assert r.status_code == 503
        assert BulkImportJob.objects.get().status == "failed"

    def test_purge_old_jobs(self, user):
        from datetime import timedelta

        from django.utils import timezone

        job = BulkImportJob.objects.create(owner=user)
        BulkImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=40))
        assert purge_old_import_jobs() == 1
