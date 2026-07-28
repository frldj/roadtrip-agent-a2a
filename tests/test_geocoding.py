"""Tests du géocodage Nominatim (HTTP mocké avec respx)."""
import httpx
import pytest
import respx

from common.geocoding import _cache, geocode

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@pytest.fixture(autouse=True)
def reset_geocoding_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.mark.asyncio
async def test_geocode_paris():
    with respx.mock:
        respx.get(_NOMINATIM_URL).mock(
            return_value=httpx.Response(
                200,
                json=[{"lat": "48.8566", "lon": "2.3522", "display_name": "Paris, France"}],
            )
        )
        lat, lon = await geocode("Paris")
    assert lat == pytest.approx(48.8566)
    assert lon == pytest.approx(2.3522)


@pytest.mark.asyncio
async def test_geocode_result_is_cached():
    with respx.mock:
        route = respx.get(_NOMINATIM_URL).mock(
            return_value=httpx.Response(
                200, json=[{"lat": "48.8566", "lon": "2.3522"}]
            )
        )
        await geocode("Paris")
        await geocode("Paris")  # doit venir du cache
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_geocode_different_cities_not_shared():
    with respx.mock:
        route = respx.get(_NOMINATIM_URL).mock(
            return_value=httpx.Response(
                200, json=[{"lat": "48.0", "lon": "2.0"}]
            )
        )
        await geocode("Paris")
        await geocode("Lyon")
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_geocode_unknown_place_raises():
    with respx.mock:
        respx.get(_NOMINATIM_URL).mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(ValueError, match="introuvable"):
            await geocode("XyzLieuInconnu999")


@pytest.mark.asyncio
async def test_geocode_returns_float_tuple():
    with respx.mock:
        respx.get(_NOMINATIM_URL).mock(
            return_value=httpx.Response(
                200, json=[{"lat": "43.2965", "lon": "5.3698"}]
            )
        )
        lat, lon = await geocode("Marseille")
    assert isinstance(lat, float)
    assert isinstance(lon, float)
