"""Tests du modèle physique EV et de l'API OpenTopoData."""
import httpx
import pytest
import respx

from common.elevation import _cache, consumption_factor, elevation_gain_loss

_TOPO_URL = "https://api.opentopodata.org/v1/srtm90m"


class TestConsumptionFactor:
    """Tests du calcul de facteur de consommation (fonction pure, sans HTTP)."""

    def test_flat_terrain_returns_one(self):
        assert consumption_factor(0.0, 0.0, 100.0, 17.0) == pytest.approx(1.0)

    def test_uphill_increases_consumption(self):
        f = consumption_factor(500.0, 0.0, 50.0, 17.0)
        assert f > 1.0

    def test_downhill_regen_decreases_consumption(self):
        f = consumption_factor(0.0, 500.0, 50.0, 17.0)
        assert f < 1.0

    def test_uphill_1000m_50km(self):
        # 1000 m de dénivelé positif sur 50 km : facteur attendu > 1.5
        f = consumption_factor(1000.0, 0.0, 50.0, 17.0)
        assert f > 1.5

    def test_clamp_max_at_2_5(self):
        f = consumption_factor(50_000.0, 0.0, 1.0, 17.0)
        assert f == pytest.approx(2.5)

    def test_clamp_min_at_0_5(self):
        f = consumption_factor(0.0, 50_000.0, 1.0, 17.0)
        assert f == pytest.approx(0.5)

    def test_zero_distance_returns_one(self):
        assert consumption_factor(500.0, 0.0, 0.0, 17.0) == pytest.approx(1.0)

    def test_zero_consumption_returns_one(self):
        assert consumption_factor(500.0, 0.0, 100.0, 0.0) == pytest.approx(1.0)

    def test_symmetry_partial(self):
        # Même dénivelé positif et négatif : résultat net dépend du rendement regen
        gain = 300.0
        loss = 300.0
        f = consumption_factor(gain, loss, 50.0, 17.0)
        # regen < 100 % donc facteur légèrement > 1.0
        assert f > 1.0


class TestElevationGainLoss:
    """Tests de l'appel HTTP à OpenTopoData (httpx mocké avec respx)."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        _cache.clear()
        yield
        _cache.clear()

    @pytest.mark.asyncio
    async def test_monotonic_ascent(self):
        elevs = [100 + i * 50 for i in range(8)]  # 100, 150, …, 450
        results = [{"elevation": e} for e in elevs]
        with respx.mock:
            respx.get(_TOPO_URL).mock(
                return_value=httpx.Response(200, json={"results": results})
            )
            gain, loss = await elevation_gain_loss((48.0, 2.0), (47.0, 3.0))
        assert gain == pytest.approx(350.0)
        assert loss == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_monotonic_descent(self):
        elevs = [450 - i * 50 for i in range(8)]  # 450, 400, …, 100
        results = [{"elevation": e} for e in elevs]
        with respx.mock:
            respx.get(_TOPO_URL).mock(
                return_value=httpx.Response(200, json={"results": results})
            )
            gain, loss = await elevation_gain_loss((48.0, 2.0), (47.0, 3.0))
        assert gain == pytest.approx(0.0)
        assert loss == pytest.approx(350.0)

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_http_error(self):
        with respx.mock:
            respx.get(_TOPO_URL).mock(return_value=httpx.Response(500))
            gain, loss = await elevation_gain_loss((48.0, 2.0), (47.0, 3.0))
        assert gain == 0.0
        assert loss == 0.0

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_network_error(self):
        with respx.mock:
            respx.get(_TOPO_URL).mock(side_effect=httpx.ConnectError("timeout"))
            gain, loss = await elevation_gain_loss((48.0, 2.0), (47.0, 3.0))
        assert gain == 0.0
        assert loss == 0.0

    @pytest.mark.asyncio
    async def test_result_is_cached(self):
        results = [{"elevation": 100}] * 8
        with respx.mock:
            route = respx.get(_TOPO_URL).mock(
                return_value=httpx.Response(200, json={"results": results})
            )
            await elevation_gain_loss((48.0, 2.0), (47.0, 3.0))
            await elevation_gain_loss((48.0, 2.0), (47.0, 3.0))
        assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_different_endpoints_not_cached_together(self):
        results = [{"elevation": 100}] * 8
        with respx.mock:
            route = respx.get(_TOPO_URL).mock(
                return_value=httpx.Response(200, json={"results": results})
            )
            await elevation_gain_loss((48.0, 2.0), (47.0, 3.0))
            await elevation_gain_loss((43.0, 5.0), (44.0, 6.0))
        assert route.call_count == 2
