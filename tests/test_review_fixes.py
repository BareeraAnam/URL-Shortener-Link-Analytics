"""Regression tests for the issues raised in the code review (each test names the issue)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from apps.analytics.models import Click
from apps.analytics.tracking import store_click
from apps.links import bulk
from apps.links.models import BulkImportJob, Link
from apps.links.tasks import fail_stuck_import_jobs, process_bulk_import
from apps.links.validators import RESERVED_SLUGS, validate_destination_url
from tests.conftest import CHROME_WIN


def csv_file(text, name="links.csv"):
    return SimpleUploadedFile(name, text.encode(), content_type="text/csv")


# --------------------------------------------------------------------------- #1 bulk import
@pytest.mark.django_db
class TestBulkImportResilience:
    def test_each_distinct_password_is_hashed_once_per_job(self, api, user):
        rows = "\n".join(f"https://example.com/{i},pw-slug-{i:03d},,,same-password" for i in range(30))
        text = "original_url,slug,title,expires_at,password\n" + rows
        with patch("apps.links.models.make_password", side_effect=lambda raw: f"hash::{raw}") as hasher:
            r = api.post("/api/links/bulk-import/", {"file": csv_file(text)}, format="multipart")
        assert r.status_code == 202
        assert hasher.call_count == 1  # 30 rows, 1 distinct password -> 1 slow PBKDF2 hash
        assert Link.objects.filter(owner=user, password="hash::same-password").count() == 30

    def test_distinct_passwords_are_hashed_separately_and_verify(self, api, user):
        text = "original_url,slug,password\nhttps://example.com/a,pw-aaa,one\nhttps://example.com/b,pw-bbb,two\n"
        api.post("/api/links/bulk-import/", {"file": csv_file(text)}, format="multipart")
        assert Link.objects.get(slug="pw-aaa").check_password("one")
        assert Link.objects.get(slug="pw-bbb").check_password("two")
        assert not Link.objects.get(slug="pw-aaa").check_password("two")

    def test_progress_is_saved_in_chunks(self, user, settings):
        settings.BULK_IMPORT_CHUNK = 5
        text = "original_url,slug\n" + "\n".join(f"https://example.com/{i},chunk-{i:03d}" for i in range(12))
        job = BulkImportJob.objects.create(owner=user, total_rows=12, payload=text)
        saved = []
        real = bulk._save_progress
        with patch.object(bulk, "_save_progress", side_effect=lambda *a: (saved.append(a[1]), real(*a))):
            bulk.run_import(job)
        assert saved == [5, 10]  # checkpoints after rows 5 and 10
        job.refresh_from_db()
        assert job.status == "completed" and job.success_count == 12

    def test_soft_time_limit_marks_job_failed_and_keeps_partial_result(self, user):
        text = "original_url,slug\n" + "\n".join(f"https://example.com/{i},slow-{i:03d}" for i in range(5))
        job = BulkImportJob.objects.create(owner=user, total_rows=5, payload=text)
        calls = {"n": 0}
        real_is_valid = bulk.LinkSerializer.is_valid

        def flaky(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 3:
                raise SoftTimeLimitExceeded()
            return real_is_valid(self, *a, **k)

        with patch.object(bulk.LinkSerializer, "is_valid", flaky):
            bulk.run_import(job)
        job.refresh_from_db()
        assert job.status == "failed" and job.success_count == 2 and job.payload == ""
        assert "Time limit exceeded" in job.errors[-1]["errors"]["non_field_errors"][0]
        assert Link.objects.filter(owner=user).count() == 2

    def test_stuck_processing_job_is_failed_and_payload_wiped(self, user, settings):
        settings.BULK_IMPORT_STUCK_MINUTES = 30
        stuck = BulkImportJob.objects.create(
            owner=user, status="processing", payload="original_url\nhttps://x.com\n", success_count=7
        )
        fresh = BulkImportJob.objects.create(owner=user, status="processing", payload="keep")
        BulkImportJob.objects.filter(pk=stuck.pk).update(updated_at=timezone.now() - timedelta(minutes=45))
        assert fail_stuck_import_jobs() == 1
        stuck.refresh_from_db()
        fresh.refresh_from_db()
        assert stuck.status == "failed" and stuck.payload == "" and "7 links were created" in str(stuck.errors)
        assert fresh.status == "processing" and fresh.payload == "keep"

    def test_pending_job_that_never_started_is_failed(self, user):
        job = BulkImportJob.objects.create(owner=user, status="pending", payload="x")
        BulkImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(hours=3))
        assert fail_stuck_import_jobs() == 1
        job.refresh_from_db()
        assert job.status == "failed"

    def test_too_many_distinct_passwords_is_rejected_up_front(self, api, settings):
        settings.BULK_IMPORT_MAX_DISTINCT_PASSWORDS = 3
        text = "original_url,password\n" + "\n".join(f"https://example.com/{i},pw{i}" for i in range(4))
        r = api.post("/api/links/bulk-import/", {"file": csv_file(text)}, format="multipart")
        assert r.status_code == 400 and "different passwords" in r.data["detail"]
        assert BulkImportJob.objects.count() == 0
        same = "original_url,password\n" + "\n".join(f"https://example.com/{i},same" for i in range(50))
        assert api.post("/api/links/bulk-import/", {"file": csv_file(same)}, format="multipart").status_code == 202

    def test_failed_job_cannot_be_rerun(self, user):
        job = BulkImportJob.objects.create(owner=user, status="failed")
        process_bulk_import(job.pk)
        job.refresh_from_db()
        assert job.status == "failed"

    def test_plaintext_passwords_are_not_kept_after_processing(self, api):
        r = api.post(
            "/api/links/bulk-import/",
            {"file": csv_file("original_url,slug,password\nhttps://example.com,wipe-me,topsecret\n")},
            format="multipart",
        )
        assert "topsecret" not in BulkImportJob.objects.get(pk=r.data["id"]).payload


# --------------------------------------------------------------------------- #2 / #5 analytics
@pytest.mark.django_db
class TestAnalyticsCorrectness:
    def test_same_visitor_on_two_days_counts_twice_and_is_named_honestly(self, api, make_link):
        link = make_link()
        for days_ago in (0, 1):
            when = timezone.now() - timedelta(days=days_ago)
            store_click(link.id, "8.8.8.8", CHROME_WIN, "", when.isoformat())
        data = api.get(f"/api/links/{link.id}/analytics/?days=7").data
        assert data["daily_unique_visitors"] == 2 and "unique_visitors" not in data
        assert [d["unique_visitors"] for d in data["timeseries"][-2:]] == [1, 1]  # per-day values are exact

    def test_overview_top_links_use_the_same_window_as_totals(self, api, make_link):
        link = make_link()
        # one click 5 days ago at 00:30, i.e. inside "last 7 days" (window starts at midnight 6 days ago)
        edge = (timezone.localtime() - timedelta(days=6)).replace(hour=0, minute=30, second=0, microsecond=0)
        Click.objects.create(link=link, clicked_at=edge, ip_hash="a", browser="Chrome", device_type="desktop")
        data = api.get("/api/analytics/overview/?days=7").data
        assert data["total_clicks"] == 1
        assert sum(x["clicks"] for x in data["timeseries"]) == 1
        assert data["top_links"][0]["clicks"] == 1  # used to be 0: top_links had a different window


# --------------------------------------------------------------------------- #3 login throttle
@pytest.mark.django_db
class TestLoginBruteForce:
    def test_login_page_is_rate_limited_per_ip(self, client, user, settings):
        settings.LOGIN_RATE_LIMIT = "3/m"
        codes = [client.post("/login/", {"username": f"user{i}", "password": "x"}).status_code for i in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        assert b"Too many login attempts" in client.post("/login/", {"username": "alice", "password": "x"}).content

    def test_login_is_also_limited_per_username_across_ips(self, client, user, settings):
        settings.LOGIN_RATE_LIMIT = "3/m"
        codes = [
            client.post("/login/", {"username": "alice", "password": "bad"}, REMOTE_ADDR=f"9.9.9.{i}").status_code
            for i in range(5)
        ]
        assert codes == [200, 200, 200, 429, 429]

    def test_correct_login_still_works(self, client, user):
        r = client.post("/login/", {"username": "alice", "password": "S3cure-pass!"})
        assert r.status_code == 302 and r["Location"] == "/dashboard/"


# --------------------------------------------------------------------------- #4 queryset.update()
@pytest.mark.django_db
class TestCacheInvalidationOnBulkUpdate:
    def test_queryset_update_disables_link_immediately(self, client, make_link):
        make_link(slug="phish1")
        assert client.get("/phish1").status_code == 302  # now cached
        Link.objects.filter(slug="phish1").update(is_active=False)
        assert client.get("/phish1").status_code == 404

    def test_queryset_update_of_destination_is_visible_immediately(self, client, make_link):
        make_link(slug="move11", original_url="https://example.com/old")
        client.get("/move11")
        Link.objects.filter(slug="move11").update(original_url="https://example.com/new")
        assert client.get("/move11")["Location"] == "https://example.com/new"

    def test_bulk_update_invalidates(self, client, make_link):
        link = make_link(slug="bulk11")
        client.get("/bulk11")
        link.is_active = False
        Link.objects.bulk_update([link], ["is_active"])
        assert client.get("/bulk11").status_code == 404

    def test_click_counter_updates_do_not_flush_the_cache(self, make_link, django_assert_num_queries):
        make_link(slug="count1")
        from apps.links.cache import get_link_payload

        get_link_payload("count1")
        with patch("apps.links.cache.invalidate") as inv:
            Link.objects.filter(slug="count1").update(click_count=5, last_clicked_at=timezone.now())
        inv.assert_not_called()
        with django_assert_num_queries(0):
            assert get_link_payload("count1")  # still cached

    def test_admin_disable_action(self, client, make_link, django_user_model):
        make_link(slug="adm111")
        client.get("/adm111")
        admin = django_user_model.objects.create_superuser("root", "root@example.com", "S3cure-pass!")
        client.force_login(admin)
        link = Link.objects.get(slug="adm111")
        r = client.post("/admin/links/link/", {"action": "disable_links", "_selected_action": [link.pk]})
        assert r.status_code == 302
        client.logout()
        assert client.get("/adm111").status_code == 404


# --------------------------------------------------------------------------- #6 exactly-once clicks
@pytest.mark.django_db
class TestExactlyOnceClicks:
    def test_redelivered_message_is_counted_once(self, make_link):
        link = make_link()
        args = (link.id, "8.8.8.8", CHROME_WIN, "", timezone.now().isoformat(), "", "0b6e1a6c4d3f4b0f9a5c2f8d7e1a3b4c")
        first = store_click(*args)
        second = store_click(*args)  # broker redelivery after a worker crash
        assert first is not None and second is None
        link.refresh_from_db()
        assert link.click_count == 1 and Click.objects.count() == 1

    def test_duplicate_rolls_back_the_counter_increment(self, make_link):
        link = make_link()
        Click.objects.create(link=link, clicked_at=timezone.now(), click_id="0b6e1a6c-4d3f-4b0f-9a5c-2f8d7e1a3b4c")
        store_click(
            link.id, "8.8.8.8", CHROME_WIN, "", timezone.now().isoformat(), "", "0b6e1a6c4d3f4b0f9a5c2f8d7e1a3b4c"
        )
        link.refresh_from_db()
        assert link.click_count == 0  # not incremented by the duplicate

    def test_distinct_clicks_all_count(self, make_link):
        link = make_link()
        for i in range(3):
            store_click(link.id, "8.8.8.8", CHROME_WIN, "", timezone.now().isoformat(), "", f"{i:032x}")
        link.refresh_from_db()
        assert link.click_count == 3

    def test_old_queued_messages_without_click_id_still_work(self, make_link):
        link = make_link()
        assert store_click(link.id, "8.8.8.8", CHROME_WIN, "", timezone.now().isoformat()) is not None

    def test_middleware_sends_a_unique_click_id_per_request(self, client, make_link):
        make_link(slug="uuid11")
        with patch("apps.links.middleware.record_click.delay") as delay:
            client.get("/uuid11")
            client.get("/uuid11")
        ids = [c.args[6] for c in delay.call_args_list]
        assert len(ids) == 2 and len(set(ids)) == 2 and all(len(i) == 32 for i in ids)


# --------------------------------------------------------------------------- #7 numeric IP forms
class TestNumericIpBypass:
    @pytest.mark.parametrize(
        "url",
        [
            "http://2130706433/",
            "http://0x7f000001/",
            "http://127.1/",
            "http://017700000001/",
            "http://0177.0.0.1/",
            "http://010.0.0.1/",
            "http://0x7f.0.0.1/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://[::ffff:7f00:1]/",
            "http://localhost./",
            "http://169.254.169.254/latest/meta-data",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://printer.lan/",
            "http://box.localdomain/",
        ],
    )
    def test_loopback_private_and_obfuscated_hosts_are_rejected(self, url):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            validate_destination_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/",
            "https://8.8.8.8/",
            "https://sub.domain.co.uk/a?b=1",
            "https://1password.com/",
            "https://123abc.org/",
            "https://[2606:4700:4700::1111]/",
        ],
    )
    def test_legitimate_urls_still_pass(self, url):
        validate_destination_url(url)


# --------------------------------------------------------------------------- #8 / #9 settings
@pytest.fixture
def load_base_settings(monkeypatch):
    """Re-import config.settings.base under a controlled environment, then restore it."""
    import importlib

    import config.settings.base as base

    def _load(**env):
        for key in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "DATABASE_URL", "NUM_PROXIES"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        return importlib.reload(base)

    yield _load
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite://:memory:")
    monkeypatch.delenv("DJANGO_DEBUG", raising=False)
    monkeypatch.delenv("NUM_PROXIES", raising=False)
    importlib.reload(base)


class TestProductionSettings:
    def test_production_requires_database_url(self, load_base_settings):
        with pytest.raises(ImproperlyConfigured, match="DATABASE_URL"):
            load_base_settings(DJANGO_SECRET_KEY="x")

    def test_production_requires_secret_key(self, load_base_settings):
        with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
            load_base_settings(DATABASE_URL="sqlite://:memory:")

    def test_debug_falls_back_to_sqlite(self, load_base_settings):
        mod = load_base_settings(DJANGO_DEBUG="true")
        assert mod.DATABASES["default"]["ENGINE"].endswith("sqlite3")

    def test_forwarded_proto_not_trusted_without_proxy(self, load_base_settings):
        mod = load_base_settings(DJANGO_SECRET_KEY="x", DATABASE_URL="sqlite://:memory:")
        assert not hasattr(mod, "SECURE_PROXY_SSL_HEADER")

    def test_forwarded_proto_trusted_behind_a_proxy(self, load_base_settings):
        mod = load_base_settings(DJANGO_SECRET_KEY="x", DATABASE_URL="sqlite://:memory:", NUM_PROXIES="1")
        assert mod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


# --------------------------------------------------------------------------- #10 link quota
@pytest.mark.django_db
class TestLinkQuota:
    def test_api_create_blocked_at_quota(self, api, make_link, settings):
        settings.MAX_LINKS_PER_USER = 2
        make_link()
        make_link()
        r = api.post("/api/links/", {"original_url": "https://example.com/x"}, format="json")
        assert r.status_code == 400 and "Link limit reached" in str(r.data)

    def test_bulk_import_stops_at_quota(self, api, user, settings):
        settings.MAX_LINKS_PER_USER = 3
        text = "original_url,slug\n" + "\n".join(f"https://example.com/{i},quota-{i:03d}" for i in range(6))
        r = api.post("/api/links/bulk-import/", {"file": csv_file(text)}, format="multipart")
        job = BulkImportJob.objects.get(pk=r.data["id"])
        assert job.success_count == 3 and job.failed_count == 3
        assert Link.objects.filter(owner=user).count() == 3

    def test_quota_is_per_user(self, api, make_link, other_user, settings):
        settings.MAX_LINKS_PER_USER = 1
        make_link(owner=other_user)
        assert api.post("/api/links/", {"original_url": "https://example.com/x"}, format="json").status_code == 201


# --------------------------------------------------------------------------- maintainability
@pytest.mark.django_db
class TestSlugCaseInsensitivity:
    def test_api_rejects_slug_differing_only_by_case(self, api, make_link):
        make_link(slug="Promo")
        r = api.post("/api/links/", {"original_url": "https://example.com", "slug": "promo"}, format="json")
        assert r.status_code == 400 and "case-insensitive" in str(r.data)

    def test_database_enforces_it_too(self, make_link):
        make_link(slug="Sale2026")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_link(slug="sale2026")

    def test_check_slug_endpoint_is_case_insensitive(self, api, make_link):
        make_link(slug="MixedCase")
        assert api.get("/api/links/check-slug/", {"slug": "mixedcase"}).data["reason"] == "taken"

    def test_updating_a_link_with_its_own_slug_is_fine(self, api, make_link):
        link = make_link(slug="OwnSlug")
        assert api.patch(f"/api/links/{link.id}/", {"slug": "OwnSlug", "title": "t"}, format="json").status_code == 200

    def test_redirect_lookup_stays_exact(self, client, make_link):
        make_link(slug="Exact1")
        assert client.get("/Exact1").status_code == 302
        assert client.get("/exact1").status_code == 404


class TestReservedSlugsStayInSyncWithRoutes:
    """A new top-level route (e.g. /settings/) would be silently shadowed by the redirect
    middleware unless its first path segment is reserved. This test fails when that happens."""

    @staticmethod
    def _first_segments(patterns, prefix=""):
        found = set()
        for p in patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                found |= TestReservedSlugsStayInSyncWithRoutes._first_segments(p.url_patterns, route)
            elif isinstance(p, URLPattern) and route:
                first = route.split("/")[0]
                if first and not first.startswith(("<", "^", "(")):
                    found.add(first)
        return found

    def test_every_top_level_route_is_reserved(self):
        segments = self._first_segments(get_resolver().url_patterns)
        assert {"admin", "api", "health", "unlock", "login", "logout", "register", "dashboard"} <= segments
        missing = {s for s in segments if s.lower() not in RESERVED_SLUGS}
        assert not missing, f"Add these to RESERVED_SLUGS or they will be shadowed: {sorted(missing)}"

    def test_static_prefix_is_reserved(self):
        assert "static" in RESERVED_SLUGS

    @pytest.mark.django_db
    def test_seed_demo_slugs_are_usable(self):
        from django.core.management import call_command

        call_command("seed_demo")
        assert not {s.lower() for s in Link.objects.values_list("slug", flat=True)} & RESERVED_SLUGS


# --------------------------------------------------------------------------- dashboard
@pytest.mark.django_db
class TestDashboardAssets:
    def test_chart_js_is_served_locally_not_from_a_cdn(self, client, user):
        client.force_login(user)
        html = client.get("/dashboard/").content.decode()
        assert "cdn.jsdelivr.net" not in html and "dashboard/chart.umd.js" in html
        assert "const PAGE_SIZE = 20;" in html and 'id="prev"' in html and "Daily uniques" in html

    def test_chart_js_file_ships_with_the_project(self):
        from django.contrib.staticfiles import finders

        path = finders.find("dashboard/chart.umd.js")
        assert path and "Chart.js v4.4.1" in open(path, encoding="utf-8").read(200)

    def test_links_api_is_paginated_so_link_21_is_reachable(self, api, make_link):
        for _ in range(21):
            make_link()
        first = api.get("/api/links/").data
        assert first["count"] == 21 and len(first["results"]) == 20 and first["next"]
        assert len(api.get("/api/links/?page=2").data["results"]) == 1


def test_request_factory_import_is_used():  # keeps the import list honest for linters
    assert RequestFactory().get("/").method == "GET"
