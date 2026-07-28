"""Tests pour vehicle_agent : filtre directionnel et recharge de nuit."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Le SDK a2a n'est pas installé dans l'environnement de test.
# On stub les modules avant que vehicle_agent.core ne les importe via
# common.a2a_client_utils.
for _mod_name in ("a2a", "a2a.client", "a2a.types", "a2a.utils"):
    sys.modules.setdefault(_mod_name, MagicMock())

import pytest

from common.schemas import ChargingRequest, RouteSegment, VehicleType
from vehicle_agent.core import (
    _best_forward,
    _best_ocm_forward,
    _is_forward,
    _station_coords,
    plan_charging,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seg(
    day: int,
    distance_km: float = 100.0,
    start_lat: float = 48.0,
    start_lon: float = 2.0,
    end_lat: float = 47.1,  # ~100 km au sud
    end_lon: float = 2.0,
) -> RouteSegment:
    return RouteSegment(
        day_index=day,
        start_location=f"Départ J{day}",
        end_location=f"Arrivée J{day}",
        distance_km=distance_km,
        duration_minutes=distance_km / 80 * 60,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
    )


def _req(**kwargs) -> ChargingRequest:
    defaults: dict = dict(
        vehicle_type=VehicleType.ELECTRIC,
        segments=[_seg(1), _seg(2, start_lat=47.1, end_lat=46.2)],
        battery_capacity_kwh=60.0,
        consumption_kwh_per_100km=15.0,  # autonomie = 400 km
        current_charge_percent=100.0,
        min_arrival_charge_percent=15.0,
        max_charge_stop_percent=80.0,
        overnight_charging=True,
        overnight_charge_to_percent=90.0,
    )
    defaults.update(kwargs)
    return ChargingRequest(**defaults)


# ── _station_coords ───────────────────────────────────────────────────────────

class TestStationCoords:
    def test_node_with_lat_lon(self):
        el = {"lat": 48.5, "lon": 2.3, "tags": {}}
        assert _station_coords(el) == (48.5, 2.3)

    def test_way_with_center(self):
        el = {"type": "way", "center": {"lat": 45.0, "lon": 1.0}}
        assert _station_coords(el) == (45.0, 1.0)

    def test_missing_coords_returns_none(self):
        el = {"tags": {"name": "Borne"}}
        assert _station_coords(el) is None

    def test_prefers_lat_lon_over_center(self):
        el = {"lat": 48.0, "lon": 2.0, "center": {"lat": 99.0, "lon": 99.0}}
        assert _station_coords(el) == (48.0, 2.0)


# ── _is_forward ───────────────────────────────────────────────────────────────
# Centre : 48.0°N, 2.0°E  →  Destination : 45.0°N, 2.0°E (plein sud, ~333 km)

class TestIsForward:
    # Centre de recherche et destination pour tous les tests
    clat, clon = 48.0, 2.0
    dlat, dlon = 45.0, 2.0

    def test_station_in_front_is_valid(self):
        # 55 km au sud du centre → dans la bonne direction
        assert _is_forward(47.5, 2.0, self.clat, self.clon, self.dlat, self.dlon)

    def test_station_perpendicular_is_valid(self):
        # À l'est, pas de composante négative sur l'axe N→S
        assert _is_forward(48.0, 2.5, self.clat, self.clon, self.dlat, self.dlon)

    def test_station_slightly_behind_within_threshold(self):
        # 33 km au nord → en arrière mais sous le seuil de 40 km
        assert _is_forward(48.3, 2.0, self.clat, self.clon, self.dlat, self.dlon)

    def test_station_far_behind_rejected(self):
        # ~55 km au nord → dépasse le seuil de 40 km
        assert not _is_forward(48.5, 2.0, self.clat, self.clon, self.dlat, self.dlon)

    def test_rennes_rejected_when_going_south(self):
        # Rennes (~48.1°N) depuis un centre près de Nantes (47.2°N) allant vers
        # San Sebastián (43.3°N) → ~100 km en sens inverse
        assert not _is_forward(48.1, -1.7, 47.2, -1.5, 43.3, -1.98)

    def test_zero_direction_vector_always_valid(self):
        # Centre == destination → dégenéré, accepter tout
        assert _is_forward(50.0, 5.0, 48.0, 2.0, 48.0, 2.0)

    def test_custom_threshold(self):
        # Station à ~33 km en arrière : acceptée avec seuil 40, rejetée avec seuil 20
        assert _is_forward(48.3, 2.0, self.clat, self.clon, self.dlat, self.dlon, max_backtrack_km=40.0)
        assert not _is_forward(48.3, 2.0, self.clat, self.clon, self.dlat, self.dlon, max_backtrack_km=20.0)


# ── _best_forward ─────────────────────────────────────────────────────────────

class TestBestForward:
    clat, clon = 47.0, 2.0
    dlat, dlon = 44.0, 2.0  # plein sud

    def _el(self, lat, lon) -> dict:
        return {"lat": lat, "lon": lon, "tags": {}}

    def test_empty_returns_none(self):
        assert _best_forward([], self.clat, self.clon, self.dlat, self.dlon) is None

    def test_single_forward_station_returned(self):
        el = self._el(46.5, 2.0)
        assert _best_forward([el], self.clat, self.clon, self.dlat, self.dlon) is el

    def test_prefers_forward_over_closer_backward(self):
        # 47.5 → 0.5° * 111 km = 55 km en arrière, dépasse le seuil de 40 km
        backward = self._el(47.5, 2.0)  # 55 km en arrière (hors seuil → pénalisé)
        forward = self._el(46.5, 2.0)   # 55 km en avant (même distance du centre)
        result = _best_forward([backward, forward], self.clat, self.clon, self.dlat, self.dlon)
        assert result is forward

    def test_picks_closest_when_all_forward(self):
        near = self._el(46.9, 2.0)   # ~11 km au sud
        far = self._el(46.0, 2.0)    # ~111 km au sud
        result = _best_forward([near, far], self.clat, self.clon, self.dlat, self.dlon)
        assert result is near

    def test_no_dest_returns_closest(self):
        near = self._el(47.1, 2.0)
        far = self._el(44.0, 2.0)
        result = _best_forward([near, far], self.clat, self.clon, None, None)
        assert result is near

    def test_all_backward_returns_least_bad(self):
        # Tous en arrière → retourne quand même le moins pire (le plus proche)
        slightly = self._el(47.5, 2.0)   # 55 km en arrière
        very = self._el(49.0, 2.0)       # 222 km en arrière
        result = _best_forward([slightly, very], self.clat, self.clon, self.dlat, self.dlon)
        assert result is slightly


# ── _best_ocm_forward ─────────────────────────────────────────────────────────

class TestBestOcmForward:
    clat, clon = 47.0, 2.0
    dlat, dlon = 44.0, 2.0

    def _ocm(self, lat, lon) -> dict:
        return {"AddressInfo": {"Latitude": lat, "Longitude": lon, "Title": "Station"}}

    def test_empty_returns_none(self):
        assert _best_ocm_forward([], self.clat, self.clon, self.dlat, self.dlon) is None

    def test_forward_preferred_over_backward(self):
        backward = self._ocm(47.5, 2.0)
        forward = self._ocm(46.5, 2.0)
        result = _best_ocm_forward([backward, forward], self.clat, self.clon, self.dlat, self.dlon)
        assert result is forward

    def test_missing_coords_handled(self):
        bad = {"AddressInfo": {}}
        good = self._ocm(46.5, 2.0)
        result = _best_ocm_forward([bad, good], self.clat, self.clon, self.dlat, self.dlon)
        assert result is good


# ── plan_charging : recharge de nuit ─────────────────────────────────────────
# Autonomie 400 km (60 kWh / 15 kWh/100km).
# Segments de 100 km → arrivée à 75% → sous le seuil overnight de 90%.

@pytest.fixture
def mock_elevation():
    """Terrain plat : facteur d'élévation = 1.0."""
    with patch("vehicle_agent.core.elevation_gain_loss", new=AsyncMock(return_value=(0.0, 0.0))):
        yield


@pytest.fixture
def mock_enrich_found():
    """Borne trouvée partout."""
    with patch("vehicle_agent.core._enrich_stop", new=AsyncMock(return_value=("Supercharger City", "Tesla"))):
        yield


@pytest.fixture
def mock_enrich_not_found():
    """Aucune borne trouvée (pour tester les branches sans station)."""
    with patch("vehicle_agent.core._enrich_stop", new=AsyncMock(return_value=("", ""))):
        with patch("vehicle_agent.core._split_via_route_agent", new=AsyncMock(return_value=None)):
            yield


class TestOvernightCharging:
    @pytest.mark.asyncio
    async def test_overnight_stop_added_after_non_final_segment(self, mock_elevation, mock_enrich_found):
        result = await plan_charging(_req())
        overnight = [s for s in result.fuel_or_charge_stops if s.is_overnight]
        assert len(overnight) == 1
        assert overnight[0].day_index == 1

    @pytest.mark.asyncio
    async def test_overnight_stop_charges_to_target(self, mock_elevation, mock_enrich_found):
        result = await plan_charging(_req(overnight_charge_to_percent=85.0))
        overnight = [s for s in result.fuel_or_charge_stops if s.is_overnight]
        assert overnight[0].charge_to_percent == pytest.approx(85.0)

    @pytest.mark.asyncio
    async def test_no_overnight_stop_on_last_segment(self, mock_elevation, mock_enrich_found):
        result = await plan_charging(_req())
        overnight = [s for s in result.fuel_or_charge_stops if s.is_overnight]
        # Seul J1 a un stop overnight, pas J2 (dernier segment)
        assert all(s.day_index != 2 for s in overnight)

    @pytest.mark.asyncio
    async def test_overnight_charging_disabled(self, mock_elevation, mock_enrich_found):
        result = await plan_charging(_req(overnight_charging=False))
        overnight = [s for s in result.fuel_or_charge_stops if s.is_overnight]
        assert overnight == []

    @pytest.mark.asyncio
    async def test_no_overnight_when_already_at_target(self, mock_elevation, mock_enrich_found):
        # Batterie à 100% et segments très courts → arrive au-dessus de 90%
        result = await plan_charging(_req(
            segments=[
                _seg(1, distance_km=10.0),
                _seg(2, distance_km=10.0, start_lat=47.91, end_lat=47.82),
            ],
            overnight_charge_to_percent=90.0,
        ))
        overnight = [s for s in result.fuel_or_charge_stops if s.is_overnight]
        assert overnight == []

    @pytest.mark.asyncio
    async def test_single_segment_no_overnight(self, mock_elevation, mock_enrich_found):
        result = await plan_charging(_req(segments=[_seg(1)]))
        overnight = [s for s in result.fuel_or_charge_stops if s.is_overnight]
        assert overnight == []

    @pytest.mark.asyncio
    async def test_overnight_stop_location_is_segment_end(self, mock_elevation, mock_enrich_found):
        result = await plan_charging(_req())
        overnight = next(s for s in result.fuel_or_charge_stops if s.is_overnight)
        # La station trouvée ("Supercharger City") remplace le hint par défaut
        assert overnight.location_hint == "Supercharger City"

    @pytest.mark.asyncio
    async def test_overnight_stop_fallback_to_city_name(self, mock_elevation, mock_enrich_not_found):
        result = await plan_charging(_req())
        overnight = next((s for s in result.fuel_or_charge_stops if s.is_overnight), None)
        if overnight:
            # Pas de borne trouvée → fallback sur le nom de ville du segment
            assert overnight.location_hint == "Arrivée J1"

    @pytest.mark.asyncio
    async def test_overnight_stop_charge_from_is_arrival_percent(self, mock_elevation, mock_enrich_found):
        # 100 km sur 400 km d'autonomie → consomme 25% → arrive à 75%
        result = await plan_charging(_req())
        overnight = next(s for s in result.fuel_or_charge_stops if s.is_overnight)
        assert overnight.charge_from_percent == pytest.approx(75.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_three_segment_trip_has_two_overnight_stops(self, mock_elevation, mock_enrich_found):
        segs = [
            _seg(1, start_lat=48.0, end_lat=47.1),
            _seg(2, start_lat=47.1, end_lat=46.2),
            _seg(3, start_lat=46.2, end_lat=45.3),
        ]
        result = await plan_charging(_req(segments=segs))
        overnight = [s for s in result.fuel_or_charge_stops if s.is_overnight]
        assert len(overnight) == 2
        assert {s.day_index for s in overnight} == {1, 2}

    @pytest.mark.asyncio
    async def test_thermal_vehicle_no_overnight_stop(self, mock_elevation):
        result = await plan_charging(_req(vehicle_type=VehicleType.THERMAL))
        assert result.fuel_or_charge_stops == []
