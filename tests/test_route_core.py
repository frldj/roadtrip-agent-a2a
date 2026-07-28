"""Tests des fonctions pures de l'agent Itinéraire."""
import pytest

from route_agent.core import _haversine_km


class TestHaversine:
    def test_paris_lyon(self):
        # Paris (48.8566, 2.3522) → Lyon (45.7640, 4.8357) ≈ 392 km
        d = _haversine_km((48.8566, 2.3522), (45.7640, 4.8357))
        assert 380 < d < 420

    def test_paris_bordeaux(self):
        # Paris → Bordeaux ≈ 500 km
        d = _haversine_km((48.8566, 2.3522), (44.8378, -0.5792))
        assert 470 < d < 530

    def test_same_point_is_zero(self):
        d = _haversine_km((48.0, 2.0), (48.0, 2.0))
        assert d == pytest.approx(0.0, abs=0.001)

    def test_symmetry(self):
        a = (48.8566, 2.3522)
        b = (45.7640, 4.8357)
        assert _haversine_km(a, b) == pytest.approx(_haversine_km(b, a), rel=1e-9)

    def test_result_is_positive(self):
        d = _haversine_km((0.0, 0.0), (1.0, 1.0))
        assert d > 0

    def test_one_degree_latitude(self):
        # 1° de latitude ≈ 111 km
        d = _haversine_km((0.0, 0.0), (1.0, 0.0))
        assert 110 < d < 112
