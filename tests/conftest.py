import os

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.links.models import Link

CHROME_WIN = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    return User.objects.create_user("alice", "alice@example.com", "S3cure-pass!")


@pytest.fixture
def other_user(db):
    return User.objects.create_user("bob", "bob@example.com", "S3cure-pass!")


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def anon_api():
    return APIClient()


@pytest.fixture
def make_link(user):
    counter = {"n": 0}

    def _make(**kwargs):
        counter["n"] += 1
        owner = kwargs.pop("owner", user)
        password = kwargs.pop("password", None)
        kwargs.setdefault("slug", f"link{counter['n']:03d}")
        kwargs.setdefault("original_url", "https://example.com/page")
        link = Link(owner=owner, **kwargs)
        link.set_password(password)
        link.save()
        return link

    return _make


def pytest_collection_modifyitems(config, items):
    """Integration tests need real Postgres/Redis; skip them unless explicitly enabled."""
    if os.environ.get("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="integration test: set RUN_INTEGRATION=1 and use --ds=config.settings.integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
