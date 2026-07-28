"""
Logique métier de l'agent Itinéraire.

Utilise :
- Nominatim (OpenStreetMap) pour le géocodage des noms de lieux -> lat/lon
- OSRM (démo publique router.project-osrm.org) pour le calcul d'itinéraire.
- Ollama (LLM local) pour suggérer des villes d'étape naturelles sur les
  trajets multi-jours sans waypoints utilisateur.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re

import httpx

from chat_client.ollama_client import ollama_chat
from common.geocoding import geocode
from common.schemas import RoutePlanRequest, RoutePlanResponse, RouteSegment

logger = logging.getLogger(__name__)

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
ROUTE_AGENT_LLM_MODEL = os.getenv("ROUTE_AGENT_LLM_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5"))

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


# ── LLM : suggestion de villes d'étape ───────────────────────────────────────

def _parse_city_list(content: str) -> list[str]:
    """
    Parse une liste de villes depuis la réponse brute d'un LLM.
    Gère les cas courants : code fences markdown, texte avant/après le JSON,
    virgules finales, guillemets simples.
    """
    # Supprime les blocs de code markdown
    text = re.sub(r"```[a-zA-Z]*\n?", "", content).strip()
    # Tentative 1 : parse JSON direct
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(c).strip() for c in parsed if str(c).strip()]
    except json.JSONDecodeError:
        pass
    # Tentative 2 : extrait le premier [...] (ignore le texte avant)
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if m:
        candidate = re.sub(r",\s*(?=])", "", m.group(0))  # supprime trailing commas
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return [str(c).strip() for c in parsed if str(c).strip()]
        except json.JSONDecodeError:
            pass
    # Tentative 3 : extrait toutes les chaînes entre guillemets
    found = re.findall(r'"([^"]{2,})"', text)
    return [c.strip() for c in found if c.strip()]


async def _llm_suggest_stopovers(
    origin: str,
    destination: str,
    total_km: float,
    total_hours: float,
    max_hours_per_day: float,
    n_days: int,
) -> list[str]:
    """
    Demande au LLM de proposer n_days-1 villes d'étape naturelles entre
    origin et destination, espacées de ~max_hours_per_day de conduite.
    Retourne une liste de noms de villes (vide si LLM indisponible).
    """
    n_stops = n_days - 1
    messages = [
        {
            "role": "system",
            "content": (
                "You are a European road-trip expert with detailed knowledge of "
                "road networks and interesting overnight stop cities."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Plan a {n_days}-day road trip from {origin} to {destination}.\n"
                f"Total: approximately {total_km:.0f} km, {total_hours:.1f} h driving. "
                f"Maximum {max_hours_per_day:.1f} h per day.\n"
                f"Suggest exactly {n_stops} intermediate overnight stop(s).\n\n"
                "Requirements:\n"
                "- Cities must be ON the direct driving route (no detours)\n"
                "- Real, well-known cities with accommodation\n"
                "- Spaced so each day is roughly equal driving time\n"
                "- Never suggest a city that goes in the opposite direction of the destination\n\n"
                'Respond ONLY with a JSON array of city names. Example: ["Bordeaux"]'
            ),
        },
    ]
    try:
        resp = await ollama_chat(ROUTE_AGENT_LLM_MODEL, messages)
        content = resp.get("message", {}).get("content", "").strip()
        return _parse_city_list(content)
    except Exception as exc:
        logger.warning("LLM stopover suggestion failed: %s", exc)
    return []


# ── Logique principale ────────────────────────────────────────────────────────

async def plan_route(req: RoutePlanRequest) -> RoutePlanResponse:
    llm_stopovers: list[str] = []
    warnings: list[str] = []

    places = [req.origin, *req.waypoints, req.destination]
    coords = [await geocode(p) for p in places]

    # ── Phase 1 : OSRM initial ────────────────────────────────────────────────
    leg_km: list[float]
    leg_min: list[float]
    leg_geom: list[list[list[float]]]

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
        leg_geom = [
            [[coords[i][0], coords[i][1]], [coords[i + 1][0], coords[i + 1][1]]]
            for i in range(len(coords) - 1)
        ]

    total_min = sum(leg_min)
    max_min = req.max_driving_hours_per_day * 60.0

    # ── Phase 2 : LLM suggest stopovers si trajet multi-jours sans waypoints ──
    # Condition : trajet multi-jours ET pas de waypoints utilisateur ET pas un
    # appel interne de vehicle_agent (max_driving_hours=24 signale un appel A2A
    # peer-to-peer qui ne doit pas être re-découpé par le LLM).
    needs_split = total_min > max_min
    is_internal_call = req.max_driving_hours_per_day >= 24.0
    has_user_waypoints = bool(req.waypoints)

    if needs_split and not is_internal_call and not has_user_waypoints:
        n_days = math.ceil(total_min / max_min)
        suggested = await _llm_suggest_stopovers(
            origin=req.origin,
            destination=req.destination,
            total_km=sum(leg_km),
            total_hours=total_min / 60.0,
            max_hours_per_day=req.max_driving_hours_per_day,
            n_days=n_days,
        )
        if suggested:
            llm_stopovers = suggested
            enriched_places = [req.origin, *suggested, req.destination]
            try:
                enriched_coords = [await geocode(p) for p in enriched_places]
                osrm_data2 = await osrm_route(enriched_coords)
                legs2 = osrm_data2["routes"][0]["legs"]
                places = enriched_places
                coords = enriched_coords
                leg_km = [leg["distance"] / 1000.0 for leg in legs2]
                leg_min = [leg["duration"] / 60.0 for leg in legs2]
                leg_geom = [_leg_latlons(leg) for leg in legs2]
            except Exception as exc:
                warnings.append(f"Étapes LLM non applicables ({exc}) — itinéraire direct conservé.")
                llm_stopovers = []
        else:
            warnings.append("LLM indisponible — découpage automatique sans étapes naturelles.")

    # ── Phase 3 : découpage en segments journaliers ───────────────────────────
    segments: list[RouteSegment] = []
    day_index = 1
    acc_km = 0.0
    acc_min = 0.0
    day_start_place = places[0]
    day_start_coords = coords[0]
    day_geom: list[list[float]] = []

    for i, (km, minutes) in enumerate(zip(leg_km, leg_min)):
        if acc_min > 0 and acc_min + minutes > max_min:
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
        llm_stopovers=llm_stopovers,
        warnings=warnings,
    )
