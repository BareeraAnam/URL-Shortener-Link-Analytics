"""Run against REAL PostgreSQL and REAL Redis (see .github/workflows/ci.yml).

    RUN_INTEGRATION=1 DATABASE_URL=postgres://... REDIS_URL=redis://... \
        pytest tests/integration --ds=config.settings.integration --no-cov
"""

import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django_redis import get_redis_connection

from apps.analytics.models import Click
from apps.analytics.tracking import store_click
from apps.links.models import Link
from tests.conftest import CHROME_WIN

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def test_running_on_real_postgres_and_redis():
    assert connection.vendor == "postgresql"
    assert get_redis_connection("default").ping()


class TestRedisCache:
    def test_redirect_stores_the_link_in_redis(self, client, make_link):
        make_link(slug="redis-1")
        assert client.get("/redis-1").status_code == 302
        keys = [k.decode() for k in get_redis_connection("default").keys("*link:v1:redis-1")]
        assert keys, "the link payload was not written to Redis"

    def test_second_redirect_needs_zero_database_queries(self, client, make_link, django_assert_num_queries):
        make_link(slug="redis-2")
        with patch("apps.links.middleware.record_click"):
            client.get("/redis-2")
            with django_assert_num_queries(0):
                assert client.get("/redis-2").status_code == 302

    def test_missing_slug_is_negatively_cached(self, client):
        assert client.get("/nothere1").status_code == 404
        assert get_redis_connection("default").keys("*link:v1:nothere1")

    def test_queryset_update_invalidates_real_redis(self, client, make_link):
        make_link(slug="redis-3")
        client.get("/redis-3")
        Link.objects.filter(slug="redis-3").update(is_active=False)
        assert client.get("/redis-3").status_code == 404

    def test_stored_payload_never_contains_the_password_hash(self, client, make_link):
        make_link(slug="redis-4", password="s3cret-pass")
        client.get("/redis-4")
        raw = get_redis_connection("default").get(next(iter(get_redis_connection("default").keys("*link:v1:redis-4"))))
        assert b"pbkdf2" not in raw and b"md5" not in raw and b"s3cret" not in raw

    def test_rate_limit_counters_live_in_redis(self, client, make_link, settings):
        settings.REDIRECT_RATE_LIMIT = "2/m"
        make_link(slug="redis-5")
        codes = [client.get("/redis-5", REMOTE_ADDR="7.7.7.7").status_code for _ in range(4)]
        assert codes == [302, 302, 429, 429]
        assert get_redis_connection("default").keys("*rl:*")


class TestRedisOutage:
    """The 'Redis down -> database fallback' claim, tested against a dead Redis instead of assumed."""

    DEAD = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://127.0.0.1:1/0",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SOCKET_CONNECT_TIMEOUT": 1,
                "SOCKET_TIMEOUT": 1,
            },
        }
    }

    def test_redirects_keep_working_from_the_database(self, client, make_link, settings):
        make_link(slug="down-1", original_url="https://example.com/still-works")
        settings.CACHES = self.DEAD
        r = client.get("/down-1")
        assert r.status_code == 302 and r["Location"] == "https://example.com/still-works"

    def test_rate_limiter_fails_open(self, client, make_link, settings):
        make_link(slug="down-2")
        settings.CACHES = self.DEAD
        settings.REDIRECT_RATE_LIMIT = "1/m"
        assert [client.get("/down-2").status_code for _ in range(3)] == [302, 302, 302]

    def test_health_reports_degraded(self, client, settings):
        settings.CACHES = self.DEAD
        r = client.get("/health/")
        assert r.status_code == 503 and r.json()["checks"] == {"database": "ok", "cache": "error"}


class TestPostgresBehaviour:
    def test_case_insensitive_slug_unique_index(self, make_link):
        make_link(slug="PgSlug")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_link(slug="pgslug")

    def test_analytics_aggregation_with_truncdate_and_distinct(self, api, make_link):
        link = make_link()
        for days_ago, ip in [(0, "1.1.1.1"), (0, "1.1.1.1"), (0, "2.2.2.2"), (1, "1.1.1.1")]:
            when = timezone.now() - timedelta(days=days_ago)
            store_click(
                link.id, ip, CHROME_WIN, "https://www.twitter.com/x", when.isoformat(), "", f"{time.time_ns():032x}"
            )
        data = api.get(f"/api/links/{link.id}/analytics/?days=7").data
        assert data["total_clicks"] == 4
        today, yesterday = data["timeseries"][-1], data["timeseries"][-2]
        assert (today["clicks"], today["unique_visitors"]) == (3, 2)
        assert (yesterday["clicks"], yesterday["unique_visitors"]) == (1, 1)
        assert data["referrers"][0] == {"label": "twitter.com", "clicks": 4}

    def test_duplicate_delivery_is_dropped_without_poisoning_the_transaction(self, make_link):
        link = make_link()
        args = (link.id, "8.8.8.8", CHROME_WIN, "", timezone.now().isoformat(), "", "ab" * 16)
        assert store_click(*args) is not None
        assert store_click(*args) is None  # IntegrityError path, on a strict transactional DB
        assert store_click(link.id, "8.8.8.8", CHROME_WIN, "", timezone.now().isoformat(), "", "cd" * 16) is not None
        link.refresh_from_db()
        assert link.click_count == 2 and Click.objects.count() == 2

    def test_bulk_import_with_real_pbkdf2_finishes_quickly(self, api, user, settings):
        """Review issue #1 with the REAL (slow) hasher: 120 password rows, one distinct password."""
        import django.contrib.auth.hashers as hashers

        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
        hashers.get_hashers.cache_clear()
        hashers.get_hashers_by_algorithm.cache_clear()
        text = "original_url,slug,password\n" + "\n".join(
            f"https://example.com/{i},real-{i:03d},shared-pass" for i in range(120)
        )
        started = time.monotonic()
        r = api.post(
            "/api/links/bulk-import/", {"file": SimpleUploadedFile("l.csv", text.encode())}, format="multipart"
        )
        elapsed = time.monotonic() - started
        assert r.status_code == 202 and r.data["success_count"] == 120
        assert Link.objects.get(slug="real-007").check_password("shared-pass")
        assert elapsed < 30, f"took {elapsed:.1f}s - without the hash cache this would be ~{120 * 0.8:.0f}s"
