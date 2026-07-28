"""Tests de la logique de validation du plan de roadtrip."""
from __future__ import annotations

import pytest

from common.schemas import PlanValidationRequest, RouteSegment
from validator_agent.core import validate_plan


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seg(day: int, duration_minutes: float = 240.0) -> RouteSegment:
    return RouteSegment(
        day_index=day,
        start_location="A",
        end_location="B",
        distance_km=200.0,
        duration_minutes=duration_minutes,
    )


def _req(**kwargs) -> PlanValidationRequest:
    defaults: dict = {
        "segments": [_seg(1), _seg(2)],
        "max_driving_hours_per_day": 6.0,
        "charging_feasible": True,
        "charging_warnings": [],
        "days_with_accommodation": [1],
        "days_needing_accommodation": [1],
    }
    defaults.update(kwargs)
    return PlanValidationRequest(**defaults)


# ── Plan valide ───────────────────────────────────────────────────────────────

class TestValidPlan:
    def test_no_issues_returns_valid(self):
        result = validate_plan(_req())
        assert result.valid is True
        assert result.issues == []

    def test_single_segment_trip_is_valid(self):
        result = validate_plan(_req(
            segments=[_seg(1)],
            days_with_accommodation=[],
            days_needing_accommodation=[],
        ))
        assert result.valid is True

    def test_segment_at_exact_limit_is_ok(self):
        # 6 h exactement → pas de warning (seuil à 1.15×)
        result = validate_plan(_req(segments=[_seg(1, 360), _seg(2, 360)]))
        assert result.valid is True
        assert not any(i.code == "SEGMENT_TOO_LONG" for i in result.issues)

    def test_segment_just_below_threshold_is_ok(self):
        # 6 h × 1.14 = 410.4 min → sous le seuil 1.15 (414 min)
        result = validate_plan(_req(segments=[_seg(1, 410), _seg(2)]))
        assert not any(i.code == "SEGMENT_TOO_LONG" for i in result.issues)


# ── Durée de conduite ─────────────────────────────────────────────────────────

class TestSegmentDuration:
    def test_segment_too_long_raises_warning(self):
        # 6 h × 1.15 = 414 min → 420 min doit déclencher un warning
        result = validate_plan(_req(segments=[_seg(1, 420), _seg(2)]))
        codes = [i.code for i in result.issues]
        assert "SEGMENT_TOO_LONG" in codes

    def test_segment_too_long_is_warning_not_error(self):
        result = validate_plan(_req(segments=[_seg(1, 500), _seg(2)]))
        issue = next(i for i in result.issues if i.code == "SEGMENT_TOO_LONG")
        assert issue.severity == "warning"
        assert issue.day_index == 1

    def test_plan_still_valid_with_only_warnings(self):
        result = validate_plan(_req(segments=[_seg(1, 500), _seg(2)]))
        assert result.valid is True  # warning ≠ error

    def test_multiple_long_segments_each_flagged(self):
        result = validate_plan(_req(segments=[_seg(1, 500), _seg(2, 500)]))
        long_issues = [i for i in result.issues if i.code == "SEGMENT_TOO_LONG"]
        assert len(long_issues) == 2
        assert {i.day_index for i in long_issues} == {1, 2}

    def test_overshoot_reported_in_message(self):
        # max 6 h = 360 min, segment 480 min → +2 h
        result = validate_plan(_req(segments=[_seg(1, 480), _seg(2)]))
        issue = next(i for i in result.issues if i.code == "SEGMENT_TOO_LONG")
        assert "+2.0 h" in issue.message

    def test_last_segment_can_be_long(self):
        # Le dernier segment est le jour d'arrivée — même règle de durée s'applique
        result = validate_plan(_req(segments=[_seg(1), _seg(2, 500)]))
        long_issues = [i for i in result.issues if i.code == "SEGMENT_TOO_LONG"]
        assert any(i.day_index == 2 for i in long_issues)


# ── Recharge ──────────────────────────────────────────────────────────────────

class TestChargingValidation:
    def test_charging_infeasible_produces_error(self):
        result = validate_plan(_req(charging_feasible=False))
        codes = [i.code for i in result.issues]
        assert "CHARGING_INFEASIBLE" in codes

    def test_charging_infeasible_makes_plan_invalid(self):
        result = validate_plan(_req(charging_feasible=False))
        assert result.valid is False

    def test_charging_infeasible_is_error_severity(self):
        result = validate_plan(_req(charging_feasible=False))
        issue = next(i for i in result.issues if i.code == "CHARGING_INFEASIBLE")
        assert issue.severity == "error"

    def test_range_insufficient_warning_produces_error(self):
        result = validate_plan(_req(
            charging_warnings=["Jour 1 : autonomie insuffisante en début de segment."]
        ))
        codes = [i.code for i in result.issues]
        assert "RANGE_INSUFFICIENT" in codes

    def test_range_insufficient_makes_plan_invalid(self):
        result = validate_plan(_req(
            charging_warnings=["Jour 1 : Autonomie Insuffisante en début de segment."]
        ))
        assert result.valid is False

    def test_route_corrected_warning_produces_info(self):
        result = validate_plan(_req(
            charging_warnings=["Aucune borne trouvée — le route_agent propose un tracé alternatif."]
        ))
        codes = [i.code for i in result.issues]
        assert "ROUTE_CORRECTED" in codes

    def test_route_corrected_is_info_not_error(self):
        result = validate_plan(_req(
            charging_warnings=["route_correction appliquée sur le segment 1."]
        ))
        issue = next(i for i in result.issues if i.code == "ROUTE_CORRECTED")
        assert issue.severity == "info"

    def test_route_corrected_does_not_invalidate_plan(self):
        result = validate_plan(_req(
            charging_warnings=["route_correction appliquée."]
        ))
        assert result.valid is True

    def test_unrelated_warning_ignored(self):
        result = validate_plan(_req(
            charging_warnings=["Paramètres batterie non fournis → hypothèses : 60 kWh."]
        ))
        assert result.issues == []


# ── Hébergement ───────────────────────────────────────────────────────────────

class TestAccommodationValidation:
    def test_missing_accommodation_produces_warning(self):
        result = validate_plan(_req(
            segments=[_seg(1), _seg(2), _seg(3)],
            days_with_accommodation=[1],       # jour 2 manquant
            days_needing_accommodation=[1, 2],
        ))
        codes = [i.code for i in result.issues]
        assert "ACCOMMODATION_MISSING" in codes

    def test_missing_accommodation_is_warning_not_error(self):
        result = validate_plan(_req(
            segments=[_seg(1), _seg(2), _seg(3)],
            days_with_accommodation=[],
            days_needing_accommodation=[1, 2],
        ))
        issue = next(i for i in result.issues if i.code == "ACCOMMODATION_MISSING")
        assert issue.severity == "warning"

    def test_missing_accommodation_does_not_invalidate_plan(self):
        result = validate_plan(_req(
            segments=[_seg(1), _seg(2), _seg(3)],
            days_with_accommodation=[],
            days_needing_accommodation=[1, 2],
        ))
        assert result.valid is True

    def test_last_segment_never_needs_accommodation(self):
        # Dernier segment = arrivée, pas besoin d'hébergement
        result = validate_plan(_req(
            segments=[_seg(1), _seg(2)],
            days_with_accommodation=[1],  # J1 ok, J2 = dernier jour
            days_needing_accommodation=[1, 2],
        ))
        accom_issues = [i for i in result.issues if i.code == "ACCOMMODATION_MISSING"]
        assert not any(i.day_index == 2 for i in accom_issues)

    def test_day_not_in_needing_list_not_flagged(self):
        # Si days_needing_accommodation est vide, aucun jour n'est flaggé
        result = validate_plan(_req(
            segments=[_seg(1), _seg(2), _seg(3)],
            days_with_accommodation=[],
            days_needing_accommodation=[],
        ))
        assert not any(i.code == "ACCOMMODATION_MISSING" for i in result.issues)

    def test_missing_day_index_in_issue(self):
        result = validate_plan(_req(
            segments=[_seg(1), _seg(2), _seg(3)],
            days_with_accommodation=[2],
            days_needing_accommodation=[1, 2],
        ))
        issue = next(i for i in result.issues if i.code == "ACCOMMODATION_MISSING")
        assert issue.day_index == 1


# ── Combinaisons ──────────────────────────────────────────────────────────────

class TestCombined:
    def test_error_and_warning_together(self):
        result = validate_plan(_req(
            segments=[_seg(1, 500), _seg(2)],
            charging_feasible=False,
        ))
        assert result.valid is False
        codes = {i.code for i in result.issues}
        assert "CHARGING_INFEASIBLE" in codes
        assert "SEGMENT_TOO_LONG" in codes

    def test_only_warnings_plan_is_valid(self):
        result = validate_plan(_req(
            segments=[_seg(1, 500), _seg(2)],
            days_with_accommodation=[],
            days_needing_accommodation=[1],
        ))
        assert result.valid is True
        assert len(result.issues) >= 2  # SEGMENT_TOO_LONG + ACCOMMODATION_MISSING

    def test_issues_list_empty_when_no_problems(self):
        result = validate_plan(_req())
        assert result.issues == []
