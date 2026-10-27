import pytest


@pytest.mark.django_db
class TestQRCode:
    def test_png_default(self, api, make_link):
        link = make_link()
        r = api.get(f"/api/links/{link.id}/qr/")
        assert r.status_code == 200 and r["Content-Type"] == "image/png"
        assert r.content.startswith(b"\x89PNG\r\n\x1a\n")

    def test_svg(self, api, make_link):
        r = api.get(f"/api/links/{make_link().id}/qr/?type=svg")
        assert r["Content-Type"] == "image/svg+xml" and b"<svg" in r.content

    def test_size_changes_image_and_download_header(self, api, make_link):
        link = make_link(slug="qrslug")
        small = api.get(f"/api/links/{link.id}/qr/?size=2").content
        big = api.get(f"/api/links/{link.id}/qr/?size=15").content
        assert len(big) > len(small)
        r = api.get(f"/api/links/{link.id}/qr/?download=1")
        assert 'attachment; filename="qrslug.png"' == r["Content-Disposition"]

    def test_encodes_the_short_url(self, api, make_link, monkeypatch):
        captured = {}
        import apps.links.views as views

        def fake(data, *a, **k):
            captured["data"] = data
            return b"x", "image/png"

        monkeypatch.setattr(views, "render_qr", fake)
        link = make_link(slug="encode1")
        api.get(f"/api/links/{link.id}/qr/")
        assert captured["data"] == "http://sho.rt/encode1"

    @pytest.mark.parametrize("q", ["size=0", "size=99", "type=gif", "border=-1"])
    def test_invalid_params(self, api, make_link, q):
        assert api.get(f"/api/links/{make_link().id}/qr/?{q}").status_code == 400

    def test_foreign_link_and_anonymous(self, api, anon_api, make_link, other_user):
        assert api.get(f"/api/links/{make_link(owner=other_user).id}/qr/").status_code == 404
        assert anon_api.get(f"/api/links/{make_link().id}/qr/").status_code == 401
