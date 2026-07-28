"""Tests de validation des schémas Pydantic."""
import pytest
from pydantic import ValidationError

from common.schemas import (
    AccommodationOption,
    AccommodationType,
    ChargingStop,
    RoadtripRequest,
    RouteSegment,
    VehicleType,
)


class TestRoadtripRequest:
    def test_minimal_fields(self):
        req = RoadtripRequest(
            origin="Paris", destination="Lyon", vehicle_type="electric"
        )
        assert req.waypoints == []
        assert req.max_driving_hours_per_day == 6.0
        assert req.accommodation_type == AccommodationType.NO_PREFERENCE
        assert req.tesla_supercharger_only is False
        assert req.budget_per_night_eur is None
        assert req.start_date is None

    def test_full_fields(self):
        req = RoadtripRequest(
            origin="Paris",
            destination="Barcelone",
            waypoints=["Lyon", "Montpellier"],
            vehicle_type="electric",
            battery_capacity_kwh=75.0,
            consumption_kwh_per_100km=16.0,
            max_driving_hours_per_day=5.0,
            accommodation_type="camping",
            budget_per_night_eur=30.0,
            start_date="2026-08-10",
            tesla_supercharger_only=True,
        )
        assert req.waypoints == ["Lyon", "Montpellier"]
        assert req.vehicle_type == VehicleType.ELECTRIC
        assert req.accommodation_type == AccommodationType.CAMPING
        assert req.tesla_supercharger_only is True

    def test_thermal_vehicle(self):
        req = RoadtripRequest(
            origin="Paris", destination="Nice", vehicle_type="thermal"
        )
        assert req.vehicle_type == VehicleType.THERMAL

    def test_invalid_vehicle_type(self):
        with pytest.raises(ValidationError):
            RoadtripRequest(origin="A", destination="B", vehicle_type="horse")

    def test_invalid_accommodation_type(self):
        with pytest.raises(ValidationError):
            RoadtripRequest(
                origin="A", destination="B",
                vehicle_type="electric", accommodation_type="tent",
            )

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            RoadtripRequest(origin="Paris", vehicle_type="electric")

    def test_no_preference_accommodation(self):
        req = RoadtripRequest(
            origin="A", destination="B",
            vehicle_type="electric", accommodation_type="no_preference",
        )
        assert req.accommodation_type == AccommodationType.NO_PREFERENCE


class TestRouteSegment:
    def test_with_coordinates(self):
        seg = RouteSegment(
            day_index=0,
            start_location="Paris",
            end_location="Lyon",
            distance_km=462.0,
            duration_minutes=270.0,
            start_lat=48.8566,
            start_lon=2.3522,
            end_lat=45.7640,
            end_lon=4.8357,
        )
        assert seg.start_lat == pytest.approx(48.8566)
        assert seg.end_lon == pytest.approx(4.8357)

    def test_without_coordinates(self):
        seg = RouteSegment(
            day_index=0,
            start_location="A",
            end_location="B",
            distance_km=100.0,
            duration_minutes=90.0,
        )
        assert seg.start_lat is None
        assert seg.start_lon is None
        assert seg.end_lat is None
        assert seg.end_lon is None

    def test_day_index_zero(self):
        seg = RouteSegment(
            day_index=0, start_location="A", end_location="B",
            distance_km=50.0, duration_minutes=45.0,
        )
        assert seg.day_index == 0


class TestChargingStop:
    def test_all_fields(self):
        stop = ChargingStop(
            day_index=1,
            location_hint="Bordeaux",
            distance_from_day_start_km=150.0,
            charge_from_percent=20.0,
            charge_to_percent=80.0,
            estimated_charging_minutes=30.0,
            station_provider_hint="Tesla Supercharger",
        )
        assert stop.station_provider_hint == "Tesla Supercharger"
        assert stop.charge_to_percent == pytest.approx(80.0)

    def test_optional_provider(self):
        stop = ChargingStop(
            day_index=0,
            location_hint="Somewhere",
            distance_from_day_start_km=100.0,
            charge_from_percent=15.0,
            charge_to_percent=80.0,
            estimated_charging_minutes=25.0,
        )
        assert stop.station_provider_hint is None


class TestAccommodationOption:
    def test_camping(self):
        opt = AccommodationOption(
            day_index=1,
            name="Camping les Pins",
            type=AccommodationType.CAMPING,
            location_hint="Bordeaux",
            estimated_price_eur=20.0,
            rating=4.2,
        )
        assert opt.type == AccommodationType.CAMPING
        assert opt.rating == pytest.approx(4.2)

    def test_optional_price_fields(self):
        opt = AccommodationOption(
            day_index=0,
            name="Hôtel du Centre",
            type=AccommodationType.HOTEL,
            location_hint="Lyon",
        )
        assert opt.estimated_price_eur is None
        assert opt.rating is None
