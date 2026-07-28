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


# ── Bypass "lat,lon" (utilisé par vehicle_agent → route_agent) ───────────────

class TestLatLonBypass:
    @pytest.mark.asyncio
    async def test_latlon_string_returns_directly(self):
        # Aucune requête HTTP ne doit être émise
        with respx.mock:
            lat, lon = await geocode("46.5000,2.3000")
        assert lat == pytest.approx(46.5)
        assert lon == pytest.approx(2.3)

    @pytest.mark.asyncio
    async def test_latlon_negative_longitude(self):
        lat, lon = await geocode("43.2965,-1.9800")
        assert lat == pytest.approx(43.2965)
        assert lon == pytest.approx(-1.98)

    @pytest.mark.asyncio
    async def test_latlon_bypass_is_cached(self):
        _cache.clear()
        await geocode("48.8566,2.3522")
        assert "48.8566,2.3522" in _cache

    @pytest.mark.asyncio
    async def test_latlon_does_not_call_nominatim(self):
        with respx.mock as mock:
            mock.get(_NOMINATIM_URL).mock(side_effect=AssertionError("Nominatim should not be called"))
            lat, lon = await geocode("45.7640,4.8357")
        assert lat == pytest.approx(45.764)
        assert lon == pytest.approx(4.8357)

    @pytest.mark.asyncio
    async def test_latlon_with_spaces_around_comma(self):
        lat, lon = await geocode("46.5 , 2.3")
        assert lat == pytest.approx(46.5)
        assert lon == pytest.approx(2.3)

    @pytest.mark.asyncio
    async def test_place_name_with_comma_falls_through_to_nominatim(self):
        # "Paris, France" contient une virgule mais ne parse pas en deux floats
        with respx.mock:
            respx.get(_NOMINATIM_URL).mock(
                return_value=httpx.Response(
                    200, json=[{"lat": "48.8566", "lon": "2.3522"}]
                )
            )
            lat, lon = await geocode("Paris, France")
        assert lat == pytest.approx(48.8566)

    @pytest.mark.asyncio
    async def test_latlon_five_decimal_places(self):
        lat, lon = await geocode("46.12345,2.67890")
        assert lat == pytest.approx(46.12345)
        assert lon == pytest.approx(2.6789)
