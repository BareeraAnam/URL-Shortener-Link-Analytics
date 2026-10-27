from datetime import timedelta

import pytest
from django.utils import timezone

from apps.links.models import Link

URL = "/api/links/"


@pytest.mark.django_db
class TestLinkCrud:
    def test_requires_authentication(self, anon_api):
        assert anon_api.get(URL).status_code == 401
        assert anon_api.post(URL, {"original_url": "https://example.com"}).status_code == 401

    def test_create_with_auto_slug(self, api):
        r = api.post(URL, {"original_url": "https://example.com/a", "title": "A"}, format="json")
        assert r.status_code == 201
        assert len(r.data["slug"]) == 7
        assert r.data["short_url"] == f"http://sho.rt/{r.data['slug']}"
        assert r.data["has_password"] is False
        assert r.data["qr_code_url"].endswith(f"/api/links/{r.data['id']}/qr/")

    def test_create_with_custom_slug(self, api):
        r = api.post(URL, {"original_url": "https://example.com", "slug": "my-promo"}, format="json")
        assert r.status_code == 201 and r.data["slug"] == "my-promo"

    @pytest.mark.parametrize("slug", ["ab", "has space", "bad/slug", "x" * 33, "ünïcode"])
    def test_invalid_slug_rejected(self, api, slug):
        r = api.post(URL, {"original_url": "https://example.com", "slug": slug}, format="json")
        assert r.status_code == 400 and "slug" in r.data

    @pytest.mark.parametrize("slug", ["admin", "API", "dashboard", "unlock", "health"])
    def test_reserved_slug_rejected(self, api, slug):
        r = api.post(URL, {"original_url": "https://example.com", "slug": slug}, format="json")
        assert r.status_code == 400

    def test_duplicate_slug_rejected(self, api, make_link):
        make_link(slug="taken1")
        r = api.post(URL, {"original_url": "https://example.com", "slug": "taken1"}, format="json")
        assert r.status_code == 400 and "slug" in r.data

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/file",
            "javascript:alert(1)",
            "http://localhost/x",
            "http://127.0.0.1/",
            "http://192.168.1.5/",
            "http://10.0.0.1",
            "https://user:pw@example.com",
            "http://sho.rt/abc",
            "http://foo.internal/",
            "not a url",
        ],
    )
    def test_dangerous_destination_rejected(self, api, url):
        r = api.post(URL, {"original_url": url}, format="json")
        assert r.status_code == 400 and "original_url" in r.data

    def test_blocked_domains_setting(self, api, settings):
        settings.BLOCKED_DOMAINS = ["evil.com"]
        for url in ("https://evil.com/x", "https://sub.evil.com/x"):
            assert api.post(URL, {"original_url": url}, format="json").status_code == 400

    def test_password_is_hashed_and_never_returned(self, api):
        r = api.post(URL, {"original_url": "https://example.com", "password": "s3cret"}, format="json")
        assert r.status_code == 201 and "password" not in r.data and r.data["has_password"] is True
        link = Link.objects.get(pk=r.data["id"])
        assert link.password != "s3cret" and link.check_password("s3cret")

    def test_expiry_must_be_future_on_create(self, api):
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        r = api.post(URL, {"original_url": "https://example.com", "expires_at": past}, format="json")
        assert r.status_code == 400 and "expires_at" in r.data

    def test_list_only_returns_own_links(self, api, make_link, other_user):
        mine = make_link()
        make_link(owner=other_user)
        r = api.get(URL)
        assert [x["id"] for x in r.data["results"]] == [mine.id]

    def test_cannot_access_foreign_link(self, api, make_link, other_user):
        foreign = make_link(owner=other_user)
        assert api.get(f"{URL}{foreign.id}/").status_code == 404
        assert api.delete(f"{URL}{foreign.id}/").status_code == 404
        assert api.patch(f"{URL}{foreign.id}/", {"title": "x"}, format="json").status_code == 404

    def test_update_and_slug_is_immutable(self, api, make_link):
        link = make_link(slug="keepme")
        r = api.patch(f"{URL}{link.id}/", {"title": "New", "original_url": "https://example.org"}, format="json")
        assert r.status_code == 200 and r.data["title"] == "New"
        r = api.patch(f"{URL}{link.id}/", {"slug": "changed"}, format="json")
        assert r.status_code == 400
        link.refresh_from_db()
        assert link.slug == "keepme"

    def test_add_and_remove_password_on_update(self, api, make_link):
        link = make_link()
        assert api.patch(f"{URL}{link.id}/", {"password": "pw1"}, format="json").data["has_password"] is True
        assert api.patch(f"{URL}{link.id}/", {"password": ""}, format="json").data["has_password"] is False

    def test_delete(self, api, make_link):
        link = make_link()
        assert api.delete(f"{URL}{link.id}/").status_code == 204
        assert not Link.objects.filter(pk=link.pk).exists()

    def test_search_and_status_filter(self, api, make_link):
        make_link(slug="alpha1", title="Summer sale")
        make_link(slug="beta22", is_active=False)
        make_link(slug="gamma3", expires_at=timezone.now() - timedelta(days=1))
        assert [x["slug"] for x in api.get(URL, {"search": "summer"}).data["results"]] == ["alpha1"]
        assert [x["slug"] for x in api.get(URL, {"status": "disabled"}).data["results"]] == ["beta22"]
        assert [x["slug"] for x in api.get(URL, {"status": "expired"}).data["results"]] == ["gamma3"]
        assert [x["slug"] for x in api.get(URL, {"status": "active"}).data["results"]] == ["alpha1"]

    def test_check_slug(self, api, make_link):
        make_link(slug="inuse1")
        check = lambda s: api.get(f"{URL}check-slug/", {"slug": s}).data  # noqa: E731
        assert check("freeslug") == {"slug": "freeslug", "available": True, "reason": None}
        assert check("inuse1")["reason"] == "taken"
        assert check("admin")["reason"] == "reserved"
        assert check("a!")["reason"] == "invalid"

    def test_create_rate_limited(self, api, settings):
        settings.LINK_CREATE_RATE_LIMIT = "2/m"
        codes = [
            api.post(URL, {"original_url": f"https://example.com/{i}"}, format="json").status_code for i in range(3)
        ]
        assert codes == [201, 201, 429]
