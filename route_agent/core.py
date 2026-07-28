"""
Logique métier de l'agent Itinéraire.

Utilise :
- Nominatim (OpenStreetMap) pour le géocodage des noms de lieux -> lat/lon
- OSRM (démo publique router.project-osrm.org) pour le calcul d'itinéraire.
"""
from __future__ import annotations

import json
import math

import httpx

from common.geocoding import geocode
from common.schemas import RoutePlanRequest, RoutePlanResponse, RouteSegment

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

_osrm_cache: dict[str, dict] = {}


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


async def osrm_route(coords: list[tuple[float, float]]) -> dict:
    key = "|".join(f"{lat},{lon}" for lat, lon in coords)
    if key in _osrm_cache:
        return _osrm_cache[key]
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_URL}/{coord_str}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            url,
            params={"overview": "false", "steps": "true", "geometries": "geojson"},
        )
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data}")
    _osrm_cache[key] = data
    return data


def _leg_latlons(leg: dict) -> list[list[float]]:
    """
    Flatten all step GeoJSON coordinates of an OSRM leg into [lat, lon] pairs.
    OSRM GeoJSON uses [lon, lat] order, so we swap here.
    Consecutive duplicate points are removed to keep the geometry clean.
    """
    points: list[list[float]] = []
    for step in leg.get("steps", []):
        for lon, lat in step.get("geometry", {}).get("coordinates", []):
            pt = [round(lat, 6), round(lon, 6)]
            if not points or points[-1] != pt:
                points.append(pt)
    return points


async def plan_route(req: RoutePlanRequest) -> RoutePlanResponse:
    places = [req.origin, *req.waypoints, req.destination]
    coords = [await geocode(p) for p in places]

    leg_geom: list[list[list[float]]] = []
    try:
        osrm_data = await osrm_route(coords)
        legs = osrm_data["routes"][0]["legs"]
        leg_km = [leg["distance"] / 1000.0 for leg in legs]
        leg_min = [leg["duration"] / 60.0 for leg in legs]
        leg_geom = [_leg_latlons(leg) for leg in legs]
    except Exception:
        leg_km = [
            _haversine_km(coords[i], coords[i + 1]) * 1.3
            for i in range(len(coords) - 1)
        ]
        leg_min = [km / 80.0 * 60.0 for km in leg_km]
        # Straight-line fallback geometry (2 points per leg)
        leg_geom = [
            [[coords[i][0], coords[i][1]], [coords[i + 1][0], coords[i + 1][1]]]
            for i in range(len(coords) - 1)
        ]

    max_minutes = req.max_driving_hours_per_day * 60.0
    segments: list[RouteSegment] = []
    day_index = 1
    acc_km = 0.0
    acc_min = 0.0
    day_start_place = places[0]
    day_start_coords = coords[0]
    day_geom: list[list[float]] = []

    for i, (km, minutes) in enumerate(zip(leg_km, leg_min)):
        if acc_min > 0 and acc_min + minutes > max_minutes:
            segments.append(
                RouteSegment(
                    day_index=day_index,
                    start_location=day_start_place,
                    end_location=places[i],
                    distance_km=round(acc_km, 1),
                    duration_minutes=round(acc_min, 1),
                    start_lat=day_start_coords[0],
                    start_lon=day_start_coords[1],
                    end_lat=coords[i][0],
                    end_lon=coords[i][1],
                    path_hint=json.dumps(day_geom) if day_geom else None,
                )
            )
            day_index += 1
            day_start_place = places[i]
            day_start_coords = coords[i]
            acc_km, acc_min = 0.0, 0.0
            day_geom = []

        day_geom.extend(leg_geom[i])
        acc_km += km
        acc_min += minutes

    segments.append(
        RouteSegment(
            day_index=day_index,
            start_location=day_start_place,
            end_location=places[-1],
            distance_km=round(acc_km, 1),
            duration_minutes=round(acc_min, 1),
            start_lat=day_start_coords[0],
            start_lon=day_start_coords[1],
            end_lat=coords[-1][0],
            end_lon=coords[-1][1],
            path_hint=json.dumps(day_geom) if day_geom else None,
        )
    )

    return RoutePlanResponse(
        segments=segments,
        total_distance_km=round(sum(leg_km), 1),
        total_duration_minutes=round(sum(leg_min), 1),
    )
