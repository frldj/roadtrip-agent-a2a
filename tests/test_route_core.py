"""Tests des fonctions pures de l'agent Itinéraire."""
import pytest

from route_agent.core import _haversine_km, _parse_city_list


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


# ── _parse_city_list ──────────────────────────────────────────────────────────

class TestParseCityList:
    def test_clean_json_array(self):
        assert _parse_city_list('["Bordeaux", "Vitoria-Gasteiz"]') == ["Bordeaux", "Vitoria-Gasteiz"]

    def test_single_city(self):
        assert _parse_city_list('["Bordeaux"]') == ["Bordeaux"]

    def test_json_in_markdown_code_fence(self):
        assert _parse_city_list('```json\n["Bordeaux"]\n```') == ["Bordeaux"]

    def test_json_after_preamble_text(self):
        content = 'Here are my suggestions:\n["Lyon", "Marseille"]'
        assert _parse_city_list(content) == ["Lyon", "Marseille"]

    def test_trailing_comma_cleaned(self):
        assert _parse_city_list('["Bordeaux", "Pau",]') == ["Bordeaux", "Pau"]

    def test_empty_array(self):
        assert _parse_city_list("[]") == []

    def test_empty_string(self):
        assert _parse_city_list("") == []

    def test_fallback_to_quoted_strings(self):
        # LLM répond en prose avec les villes entre guillemets
        content = 'I suggest stopping in "Bordeaux" and then "Bayonne".'
        result = _parse_city_list(content)
        assert "Bordeaux" in result
        assert "Bayonne" in result

    def test_strips_whitespace_in_city_names(self):
        result = _parse_city_list('["  Bordeaux  ", " Pau "]')
        assert result == ["Bordeaux", "Pau"]

    def test_ignores_empty_entries(self):
        result = _parse_city_list('["Bordeaux", "", "Pau"]')
        assert result == ["Bordeaux", "Pau"]
