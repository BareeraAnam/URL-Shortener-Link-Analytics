from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.analytics.models import Click
from apps.links.models import Link
from tests.conftest import CHROME_WIN


@pytest.mark.django_db
class TestRedirect:
    def test_redirects_with_302(self, client, make_link):
        make_link(slug="go1", original_url="https://example.com/target?a=1")
        r = client.get("/go1")
        assert r.status_code == 302 and r["Location"] == "https://example.com/target?a=1"
        assert r["Cache-Control"] == "no-store"

    def test_trailing_slash_also_works(self, client, make_link):
        make_link(slug="go2")
        assert client.get("/go2/").status_code == 302

    def test_unknown_slug_is_404(self, client):
        r = client.get("/doesnotexist")
        assert r.status_code == 404 and b"not found" in r.content.lower()

    def test_inactive_link_is_404(self, client, make_link):
        make_link(slug="off1", is_active=False)
        assert client.get("/off1").status_code == 404

    def test_expired_link_is_410(self, client, make_link):
        make_link(slug="old1", expires_at=timezone.now() - timedelta(minutes=1))
        r = client.get("/old1")
        assert r.status_code == 410 and b"expired" in r.content.lower()

    def test_link_expires_while_cached(self, client, make_link):
        link = make_link(slug="soon1", expires_at=timezone.now() + timedelta(seconds=30))
        assert client.get("/soon1").status_code == 302  # now cached
        Link.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with patch("apps.links.middleware.timezone.now", return_value=timezone.now() + timedelta(seconds=60)):
            assert client.get("/soon1").status_code == 410

    def test_reserved_and_app_paths_not_captured(self, client):
        assert client.get("/health/").status_code == 200
        assert client.get("/login/").status_code == 200
        assert client.get("/dashboard/").status_code == 302  # to login, not 404 from slug lookup

    def test_second_hit_uses_cache_zero_queries(self, client, make_link, django_assert_num_queries):
        make_link(slug="fast1")
        with patch("apps.links.middleware.record_click"):
            client.get("/fast1")  # warms cache
            with django_assert_num_queries(0):
                assert client.get("/fast1").status_code == 302

    def test_negative_cache_then_creation_is_visible(self, client, make_link):
        assert client.get("/newone").status_code == 404
        make_link(slug="newone")  # signal invalidates the negative cache entry
        assert client.get("/newone").status_code == 302

    def test_update_invalidates_cache(self, client, make_link):
        link = make_link(slug="chg11", original_url="https://example.com/old")
        assert client.get("/chg11")["Location"] == "https://example.com/old"
        link.original_url = "https://example.com/new"
        link.save()
        assert client.get("/chg11")["Location"] == "https://example.com/new"

    def test_delete_invalidates_cache(self, client, make_link):
        link = make_link(slug="del11")
        client.get("/del11")
        link.delete()
        assert client.get("/del11").status_code == 404

    def test_head_request_is_not_counted(self, client, make_link):
        link = make_link(slug="head1")
        assert client.head("/head1").status_code == 302
        assert Click.objects.filter(link=link).count() == 0

    def test_get_records_click_async(self, client, make_link):
        link = make_link(slug="trk11")
        client.get(
            "/trk11", HTTP_USER_AGENT=CHROME_WIN, HTTP_REFERER="https://www.twitter.com/post/1", REMOTE_ADDR="8.8.8.8"
        )
        link.refresh_from_db()
        assert link.click_count == 1 and link.last_clicked_at is not None
        click = Click.objects.get()
        assert click.browser == "Chrome" and click.os == "Windows" and click.device_type == "desktop"
        assert click.referrer_domain == "twitter.com"

    def test_broker_failure_never_breaks_redirect(self, client, make_link):
        make_link(slug="brk11")
        with patch("apps.links.middleware.record_click.delay", side_effect=ConnectionError("redis down")):
            assert client.get("/brk11").status_code == 302

    def test_cache_failure_falls_back_to_db(self, client, make_link):
        make_link(slug="dbf11")
        assert client.get("/dbf11").status_code == 302

    def test_redirect_rate_limit(self, client, make_link, settings):
        settings.REDIRECT_RATE_LIMIT = "3/m"
        make_link(slug="rl111")
        codes = [client.get("/rl111", REMOTE_ADDR="1.2.3.4").status_code for _ in range(5)]
        assert codes == [302, 302, 302, 429, 429]
        assert client.get("/rl111", REMOTE_ADDR="5.6.7.8").status_code == 302  # other IP unaffected

    def test_x_forwarded_for_respected_only_with_proxy_setting(self, client, make_link, settings):
        settings.REDIRECT_RATE_LIMIT = "1/m"
        settings.NUM_PROXIES = 1
        make_link(slug="xff11")
        h = lambda ip: {"HTTP_X_FORWARDED_FOR": f"6.6.6.6, {ip}", "REMOTE_ADDR": "10.0.0.1"}  # noqa: E731
        assert client.get("/xff11", **h("9.9.9.1")).status_code == 302
        assert client.get("/xff11", **h("9.9.9.2")).status_code == 302  # different real client
        assert client.get("/xff11", **h("9.9.9.1")).status_code == 429  # spoofed left entry ignored

    def test_disallowed_host_rejected(self, client, make_link):
        make_link(slug="host1")
        assert client.get("/host1", HTTP_HOST="evil.example.org").status_code == 400


@pytest.mark.django_db
class TestPasswordProtection:
    def test_redirects_to_unlock_page(self, client, make_link):
        make_link(slug="sec11", password="open-sesame")
        r = client.get("/sec11")
        assert r.status_code == 302 and r["Location"] == "/unlock/sec11/"
        assert Click.objects.count() == 0

    def test_wrong_password(self, client, make_link):
        make_link(slug="sec12", password="right")
        r = client.post("/unlock/sec12/", {"password": "wrong"})
        assert r.status_code == 401 and b"Incorrect password" in r.content
        assert client.get("/sec12")["Location"] == "/unlock/sec12/"

    def test_correct_password_unlocks_and_tracks(self, client, make_link):
        link = make_link(slug="sec13", password="right", original_url="https://example.com/secret")
        r = client.post("/unlock/sec13/", {"password": "right"})
        assert r.status_code == 302 and r["Location"] == "/sec13"
        assert "unlock_sec13" in r.cookies and r.cookies["unlock_sec13"]["httponly"]
        r = client.get("/sec13")
        assert r.status_code == 302 and r["Location"] == "https://example.com/secret"
        link.refresh_from_db()
        assert link.click_count == 1

    def test_forged_cookie_rejected(self, client, make_link):
        make_link(slug="sec14", password="right")
        client.cookies["unlock_sec14"] = "forged-value"
        assert client.get("/sec14")["Location"] == "/unlock/sec14/"

    def test_changing_password_invalidates_old_unlock(self, client, make_link):
        link = make_link(slug="sec15", password="old")
        client.post("/unlock/sec15/", {"password": "old"})
        assert client.get("/sec15")["Location"].startswith("http")
        link.set_password("new")
        link.save()
        assert client.get("/sec15")["Location"] == "/unlock/sec15/"

    def test_unlock_brute_force_rate_limited(self, client, make_link, settings):
        settings.UNLOCK_RATE_LIMIT = "3/m"
        make_link(slug="sec16", password="right")
        codes = [client.post("/unlock/sec16/", {"password": f"g{i}"}).status_code for i in range(5)]
        assert codes == [401, 401, 401, 429, 429]

    def test_unlock_page_for_public_or_missing_link(self, client, make_link):
        make_link(slug="pub11")
        assert client.get("/unlock/pub11/").status_code == 302
        assert client.get("/unlock/nothere/").status_code == 404

    def test_unlock_form_renders(self, client, make_link):
        make_link(slug="sec17", password="x")
        r = client.get("/unlock/sec17/")
        assert r.status_code == 200 and b"protected" in r.content.lower()
