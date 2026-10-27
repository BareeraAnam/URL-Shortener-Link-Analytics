import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Overrides the root fixture: never FLUSHDB a real Redis, only delete this project's own keys."""
    cache.delete_pattern("*")
    yield
    cache.delete_pattern("*")
