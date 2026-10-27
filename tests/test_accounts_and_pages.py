import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.analytics.models import Click


@pytest.mark.django_db
class TestAuthApi:
    def test_register_and_login_flow(self, anon_api):
        r = anon_api.post(
            "/api/auth/register/",
            {"username": "carol", "email": "Carol@Example.com", "password": "Str0ng-Pass-77"},
            format="json",
        )
        assert r.status_code == 201 and "password" not in r.data
        assert User.objects.get(username="carol").email == "carol@example.com"
        token = anon_api.post("/api/auth/token/", {"username": "carol", "password": "Str0ng-Pass-77"}, format="json")
        assert token.status_code == 200
        anon_api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.data['access']}")
        assert anon_api.get("/api/auth/me/").data["username"] == "carol"
        refreshed = anon_api.post("/api/auth/token/refresh/", {"refresh": token.data["refresh"]}, format="json")
        assert refreshed.status_code == 200

    def test_weak_password_and_duplicate_email(self, anon_api, user):
        weak = anon_api.post(
            "/api/auth/register/", {"username": "x1", "email": "x@e.com", "password": "123"}, format="json"
        )
        assert weak.status_code == 400
        dup = anon_api.post(
            "/api/auth/register/",
            {"username": "x2", "email": "ALICE@example.com", "password": "Str0ng-Pass-77"},
            format="json",
        )
        assert dup.status_code == 400 and "email" in dup.data

    def test_wrong_credentials_and_throttle(self, anon_api, user, settings):
        settings.AUTH_RATE_LIMIT = "3/m"
        codes = [
            anon_api.post("/api/auth/token/", {"username": "alice", "password": "bad"}, format="json").status_code
            for _ in range(4)
        ]
        assert codes == [401, 401, 401, 429]

    def test_jwt_authenticates_link_api(self, anon_api, user):
        token = anon_api.post(
            "/api/auth/token/", {"username": "alice", "password": "S3cure-pass!"}, format="json"
        ).data["access"]
        anon_api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        assert anon_api.post("/api/links/", {"original_url": "https://example.com"}, format="json").status_code == 201


@pytest.mark.django_db
class TestPages:
    def test_health(self, client):
        r = client.get("/health/")
        assert r.status_code == 200 and r.json() == {"status": "ok", "checks": {"database": "ok", "cache": "ok"}}

    def test_dashboard_requires_login(self, client):
        r = client.get("/dashboard/")
        assert r.status_code == 302 and r["Location"].startswith("/login/")

    def test_dashboard_renders_for_logged_in_user(self, client, user):
        client.force_login(user)
        r = client.get("/dashboard/")
        assert r.status_code == 200 and b"Create short link" in r.content and "csrftoken" in r.cookies

    def test_session_auth_works_for_api(self, client, user):
        client.login(username="alice", password="S3cure-pass!")
        assert client.get("/api/links/").status_code == 200

    def test_login_register_pages(self, client):
        assert client.get("/login/").status_code == 200
        assert client.get("/register/").status_code == 200
        r = client.post(
            "/register/",
            {"username": "dave", "email": "d@e.com", "password1": "Str0ng-Pass-77", "password2": "Str0ng-Pass-77"},
        )
        assert r.status_code == 302 and r["Location"] == "/dashboard/"
        assert client.get("/dashboard/").status_code == 200

    def test_register_page_validation_and_rate_limit(self, client, user, settings):
        r = client.post(
            "/register/", {"username": "z", "email": "alice@example.com", "password1": "a", "password2": "b"}
        )
        assert r.status_code == 200 and b"errorlist" in r.content
        settings.AUTH_RATE_LIMIT = "1/m"
        client.post("/register/", {})
        assert b"Too many attempts" in client.post("/register/", {}).content

    def test_logged_in_user_skips_register(self, client, user):
        client.force_login(user)
        assert client.get("/register/").status_code == 302

    def test_root_redirects_and_openapi_schema(self, client):
        assert client.get("/").status_code == 302
        assert client.get("/api/schema/").status_code == 200

    def test_seed_demo_command(self, db):
        call_command("seed_demo")
        assert Click.objects.count() == 600
        assert User.objects.get(username="demo").links.count() == 3

    def test_download_geoip_requires_key(self, monkeypatch):
        from django.core.management.base import CommandError

        monkeypatch.delenv("MAXMIND_LICENSE_KEY", raising=False)
        with pytest.raises(CommandError):
            call_command("download_geoip")


@pytest.mark.django_db
class TestEndToEnd:
    def test_create_visit_and_see_analytics(self, api, client):
        created = api.post(
            "/api/links/", {"original_url": "https://example.com/e2e", "slug": "e2e-test"}, format="json"
        ).data
        for ref in ("https://twitter.com/x", "https://twitter.com/y", ""):
            r = client.get(
                "/e2e-test",
                HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                HTTP_REFERER=ref,
                REMOTE_ADDR="8.8.4.4",
            )
            assert r.status_code == 302
        stats = api.get(f"/api/links/{created['id']}/analytics/").data
        assert stats["total_clicks"] == 3 and stats["daily_unique_visitors"] == 1
        assert stats["referrers"][0] == {"label": "twitter.com", "clicks": 2}
        assert api.get(f"/api/links/{created['id']}/").data["click_count"] == 3
