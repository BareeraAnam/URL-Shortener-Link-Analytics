from unittest.mock import patch

import pytest
from django.test import RequestFactory

from apps.core.ratelimit import is_limited
from apps.core.utils import get_client_ip, ip_key


def test_rate_limiter_fails_open_when_cache_breaks():
    request = RequestFactory().get("/")
    with patch("apps.core.ratelimit.is_ratelimited", side_effect=RuntimeError("redis down")):
        assert is_limited(request, "g", ip_key, "1/m") is False


def test_client_ip_variants(settings):
    rf = RequestFactory()
    settings.NUM_PROXIES = 0
    assert get_client_ip(rf.get("/", REMOTE_ADDR="1.1.1.1", HTTP_X_FORWARDED_FOR="9.9.9.9")) == "1.1.1.1"
    settings.NUM_PROXIES = 2
    assert get_client_ip(rf.get("/", REMOTE_ADDR="1.1.1.1", HTTP_X_FORWARDED_FOR="evil, 2.2.2.2, 3.3.3.3")) == "2.2.2.2"
    assert get_client_ip(rf.get("/", REMOTE_ADDR="1.1.1.1", HTTP_X_FORWARDED_FOR="only-one")) == "1.1.1.1"


@pytest.mark.django_db
def test_health_reports_degraded_when_cache_down(client):
    with patch("apps.core.views.cache.get", side_effect=RuntimeError):
        r = client.get("/health/")
    assert r.status_code == 503 and r.json()["checks"]["cache"] == "error"


@pytest.mark.django_db
def test_slug_generator_is_random_and_safe():
    from apps.links.services import generate_slug
    from apps.links.validators import SLUG_RE

    slugs = {generate_slug() for _ in range(500)}
    assert len(slugs) == 500 and all(SLUG_RE.match(s) for s in slugs)
    assert len(generate_slug(12)) == 12


@pytest.mark.django_db
def test_slug_collision_retries(api, make_link):
    make_link(slug="collide")
    with patch("apps.links.serializers.generate_slug", side_effect=["collide", "collide", "unique1"]):
        r = api.post("/api/links/", {"original_url": "https://example.com"}, format="json")
    assert r.status_code == 201 and r.data["slug"] == "unique1"
