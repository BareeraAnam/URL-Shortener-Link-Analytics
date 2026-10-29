"""Admin must never store a plain-text link password (the box expects a hash)."""

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.hashers import make_password

from apps.links.admin import LinkAdmin
from apps.links.models import Link


@pytest.fixture
def link_admin():
    return LinkAdmin(Link, AdminSite())


@pytest.mark.django_db
class TestLinkAdminPassword:
    def test_plain_password_typed_in_admin_is_hashed(self, link_admin, admin_user):
        link = Link(owner=admin_user, slug="adminpw", original_url="https://example.com/x", password="Abc12345")
        link_admin.save_model(request=None, obj=link, form=None, change=False)
        link.refresh_from_db()
        assert link.password != "Abc12345"
        assert link.check_password("Abc12345")
        assert not link.check_password("wrong-password")

    def test_existing_hash_is_kept_unchanged(self, link_admin, admin_user):
        existing = make_password("secret-pass")
        link = Link(owner=admin_user, slug="keephash", original_url="https://example.com/y", password=existing)
        link_admin.save_model(request=None, obj=link, form=None, change=False)
        link.refresh_from_db()
        assert link.password == existing
        assert link.check_password("secret-pass")

    def test_empty_password_stays_a_public_link(self, link_admin, admin_user):
        link = Link(owner=admin_user, slug="publiclink", original_url="https://example.com/z", password="")
        link_admin.save_model(request=None, obj=link, form=None, change=False)
        link.refresh_from_db()
        assert link.password == ""
        assert not link.has_password
