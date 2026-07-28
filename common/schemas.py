"""
Schémas de données métier échangés entre agents via A2A.

Ces objets sont sérialisés en JSON et transportés dans le champ `text`
(ou dans une DataPart) des Messages A2A. Ils ne remplacent pas le protocole
A2A lui-même (Message, Task, Part...) mais définissent le "contenu métier"
que les agents de ce projet savent produire/consommer.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class VehicleType(str, Enum):
    ELECTRIC = "electric"
    THERMAL = "thermal"


class AccommodationType(str, Enum):
    HOTEL = "hotel"
    CAMPING = "camping"
    NO_PREFERENCE = "no_preference"


class RoutePlanRequest(BaseModel):
    """Requête envoyée à l'agent Itinéraire."""

    origin: str
    destination: str
    waypoints: list[str] = Field(default_factory=list)
    max_driving_hours_per_day: float = 6.0
    start_date: Optional[str] = None  # ISO date, ex "2026-08-10"


class RouteSegment(BaseModel):
    """Un segment de trajet correspondant à une journée de conduite."""

    day_index: int
    start_location: str
    end_location: str
    distance_km: float
    duration_minutes: float
    # Coordonnées géocodées (lat/lon) des extrémités du segment
    start_lat: Optional[float] = None
    start_lon: Optional[float] = None
    end_lat: Optional[float] = None
    end_lon: Optional[float] = None
    path_hint: Optional[str] = None


class RoutePlanResponse(BaseModel):
    segments: list[RouteSegment]
    total_distance_km: float
    total_duration_minutes: float
    llm_stopovers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChargingRequest(BaseModel):
    """Requête envoyée à l'agent Véhicule pour calculer les arrêts recharge."""

    vehicle_type: VehicleType
    segments: list[RouteSegment]
    # Pour l'électrique :
    battery_capacity_kwh: Optional[float] = None
    consumption_kwh_per_100km: Optional[float] = None
    current_charge_percent: float = 100.0
    min_arrival_charge_percent: float = 15.0
    max_charge_stop_percent: float = 80.0  # charge rapide -> viser 80% typiquement
    tesla_supercharger_only: bool = False
    overnight_charging: bool = True  # recharger à chaque étape intermédiaire (nuit à l'hôtel, Camp Mode...)
    overnight_charge_to_percent: float = 90.0  # niveau cible pour la recharge de nuit


class ChargingStop(BaseModel):
    day_index: int
    location_hint: str  # ville/aire approx. où s'arrêter
    distance_from_day_start_km: float
    charge_from_percent: float
    charge_to_percent: float
    estimated_charging_minutes: float
    station_provider_hint: Optional[str] = None  # ex: "Ionity", "Tesla Supercharger"
    is_overnight: bool = False  # True = recharge de nuit à l'étape, pas en route


class ChargingPlanResponse(BaseModel):
    vehicle_type: VehicleType
    fuel_or_charge_stops: list[ChargingStop]
    feasible: bool
    warnings: list[str] = Field(default_factory=list)
    # Présent quand vehicle_agent a détecté un segment infaisable et a appelé
    # route_agent pour obtenir un découpage alternatif.
    route_correction: Optional[list["RouteSegment"]] = None


class AccommodationRequest(BaseModel):
    segments: list[RouteSegment]
    accommodation_type: AccommodationType = AccommodationType.NO_PREFERENCE
    budget_per_night_eur: Optional[float] = None


class AccommodationOption(BaseModel):
    day_index: int
    name: str
    type: AccommodationType
    location_hint: str
    estimated_price_eur: Optional[float] = None
    price_min_eur: Optional[float] = None
    price_max_eur: Optional[float] = None
    rating: Optional[float] = None


class AccommodationPlanResponse(BaseModel):
    options: list[AccommodationOption]


class ValidationIssue(BaseModel):
    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    day_index: Optional[int] = None


class PlanValidationRequest(BaseModel):
    segments: list[RouteSegment]
    max_driving_hours_per_day: float = 6.0
    charging_feasible: bool = True
    charging_warnings: list[str] = Field(default_factory=list)
    days_with_accommodation: list[int] = Field(default_factory=list)
    days_needing_accommodation: list[int] = Field(default_factory=list)


class PlanValidationResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class RoadtripRequest(BaseModel):
    """Requête utilisateur complète, reçue par l'orchestrateur."""

    origin: str
    destination: str
    waypoints: list[str] = Field(default_factory=list)
    vehicle_type: VehicleType
    battery_capacity_kwh: Optional[float] = None
    consumption_kwh_per_100km: Optional[float] = None
    max_driving_hours_per_day: float = 6.0
    accommodation_type: AccommodationType = AccommodationType.NO_PREFERENCE
    budget_per_night_eur: Optional[float] = None
    start_date: Optional[str] = None
    tesla_supercharger_only: bool = False


class RoadtripPlan(BaseModel):
    """Réponse finale assemblée par l'orchestrateur."""

    route: RoutePlanResponse
    charging: Optional[ChargingPlanResponse] = None
    accommodation: Optional[AccommodationPlanResponse] = None
    validation: Optional[PlanValidationResponse] = None
