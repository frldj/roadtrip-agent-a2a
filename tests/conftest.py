"""Fixtures partagées : reset des caches en mémoire avant chaque test."""
import pytest


@pytest.fixture(autouse=True)
def clear_caches():
    from common import geocoding, elevation

    geocoding._cache.clear()
    elevation._cache.clear()
    yield
    geocoding._cache.clear()
    elevation._cache.clear()
