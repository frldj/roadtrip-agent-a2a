"""
Logique métier de l'agent Véhicule / Énergie.

Bornes de recharge : Overpass/OSM (gratuit, sans clé).
Si OPEN_CHARGE_MAP_KEY est défini dans .env, OCM est utilisé en priorité.

Élévation : OpenTopoData (SRTM 90m, gratuit, sans clé) pour ajuster la
consommation selon le dénivelé. Dégradation gracieuse si l'API est indisponible.

Les lookups de bornes sont lancés en parallèle (asyncio.gather) ; un semaphore
limite les requêtes Overpass simultanées pour éviter le rate-limiting.
"""
from __future__ import annotations

import asyncio
import os

import httpx

from common.elevation import consumption_factor, elevation_gain_loss
from common.schemas import (
    ChargingPlanResponse,
    ChargingRequest,
    ChargingStop,
    VehicleType,
)

DEFAULT_BATTERY_KWH = 60.0
DEFAULT_CONSUMPTION_KWH_100KM = 17.0

_OVERPASS_MIRRORS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
_HEADERS = {"User-Agent": "roadtrip-a2a-demo/0.1 (contact: feriel.oulhadj@gmail.com)"}
OCM_URL = "https://api.openchargemap.io/v3/poi/"

# Limite les requêtes Overpass simultanées pour éviter le rate-limiting
_OVERPASS_SEM = asyncio.Semaphore(2)

# Caches en mémoire pour les lookups de bornes
_osm_cache: dict[tuple, dict | None] = {}
_ocm_cache: dict[tuple, dict | None] = {}


# ── Recherche OSM ─────────────────────────────────────────────────────────────

async def _find_station_osm(lat: float, lon: float, tesla_only: bool = False) -> dict | None:
    key = (round(lat, 3), round(lon, 3), tesla_only)
    if key in _osm_cache:
        return _osm_cache[key]

    if tesla_only:
        radius = 60_000
        query = (
            f"[out:json][timeout:25];"
            f"("
            f'node["amenity"="charging_station"]["socket:tesla_supercharger"](around:{radius},{lat},{lon});'
            f'node["amenity"="charging_station"]["network"~"Tesla Supercharger",i](around:{radius},{lat},{lon});'
            f'node["amenity"="charging_station"]["operator"~"Tesla",i](around:{radius},{lat},{lon});'
            f'node["amenity"="charging_station"]["brand"~"Tesla",i](around:{radius},{lat},{lon});'
            f");"
            f"out center 1;"
        )
    else:
        radius = 30_000
        query = (
            f"[out:json][timeout:20];"
            f'(node["amenity"="charging_station"](around:{radius},{lat},{lon});'
            f'way["amenity"="charging_station"](around:{radius},{lat},{lon}););'
            f"out center 1;"
        )

    result: dict | None = None
    async with _OVERPASS_SEM:
        for mirror in _OVERPASS_MIRRORS:
            try:
                async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
                    resp = await client.post(mirror, data={"data": query})
                    if resp.status_code == 200:
                        els = resp.json().get("elements", [])
                        result = els[0] if els else None
                        break
            except Exception:
                continue

    _osm_cache[key] = result
    return result


def _parse_kw(value: str) -> float | None:
    try:
        return float(value.replace(" kW", "").replace("kW", "").strip())
    except (ValueError, AttributeError):
        return None


def _osm_station_info(el: dict) -> tuple[str, str]:
    tags = el.get("tags", {})
    name = tags.get("name", "")
    operator = tags.get("operator") or tags.get("brand") or tags.get("network", "")

    op_lower = operator.lower()
    if "tesla" in op_lower or "supercharger" in name.lower():
        operator = "Tesla Supercharger"
    elif "ionity" in op_lower:
        operator = "Ionity"
    elif "totalenergies" in op_lower or "total" in op_lower:
        operator = "TotalEnergies"
    elif "engie" in op_lower:
        operator = "Engie"
    elif "leclerc" in op_lower:
        operator = "E.Leclerc"
    elif "lidl" in op_lower:
        operator = "Lidl"

    power_keys = [
        "socket:ccs:output", "socket:type2_combo:output",
        "socket:chademo:output", "socket:type2:output",
        "maxpower", "max_power",
    ]
    max_kw = max(
        (v for v in (_parse_kw(tags.get(k, "")) for k in power_keys) if v),
        default=None,
    )

    connectors = []
    if tags.get("socket:tesla_supercharger") or "Tesla" in operator:
        connectors.append("CCS/Tesla")
    elif tags.get("socket:type2_combo") or tags.get("socket:ccs"):
        connectors.append("CCS")
    if tags.get("socket:chademo"):
        connectors.append("CHAdeMO")
    if tags.get("socket:type2") and "CCS" not in " ".join(connectors):
        connectors.append("Type 2")

    location = name or operator or "Borne de recharge"
    provider_parts = [p for p in [
        operator,
        " · ".join(connectors),
        f"{max_kw:.0f} kW" if max_kw else "",
    ] if p]
    return location, " · ".join(provider_parts)


# ── Recherche OCM (optionnelle) ───────────────────────────────────────────────

async def _find_station_ocm(lat: float, lon: float) -> dict | None:
    api_key = os.getenv("OPEN_CHARGE_MAP_KEY", "").strip()
    if not api_key:
        return None

    key = (round(lat, 3), round(lon, 3))
    if key in _ocm_cache:
        return _ocm_cache[key]

    result: dict | None = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(OCM_URL, params={
                "output": "json", "latitude": lat, "longitude": lon,
                "distance": 40, "distanceunit": "KM",
                "maxresults": 1, "key": api_key,
                "compact": "true", "verbose": "false",
                "minpowerkw": 50,
                "levelid": "3",
            })
            if resp.status_code == 200:
                stations = resp.json()
                result = stations[0] if stations else None
    except Exception:
        pass

    _ocm_cache[key] = result
    return result


def _ocm_station_info(station: dict) -> tuple[str, str]:
    addr = station.get("AddressInfo", {})
    name = addr.get("Title", "")
    town = addr.get("Town", "")
    location = f"{name}, {town}".strip(", ") if name else town
    operator = (station.get("OperatorInfo") or {}).get("Title", "")
    conns = station.get("Connections") or []
    max_kw = max((c.get("PowerKW") or 0 for c in conns), default=0)
    conn_types = list({
        (c.get("ConnectionType") or {}).get("Title", "")
        for c in conns if c.get("ConnectionType")
    })
    provider_parts = [p for p in [
        operator,
        " · ".join(conn_types[:2]),
        f"{max_kw:.0f} kW" if max_kw else "",
    ] if p]
    return location, " · ".join(provider_parts)


# ── Lookup parallèle ──────────────────────────────────────────────────────────

async def _enrich_stop(lat: float, lon: float, tesla_only: bool) -> tuple[str, str]:
    station_ocm = await _find_station_ocm(lat, lon) if not tesla_only else None
    if station_ocm:
        return _ocm_station_info(station_ocm)
    station_osm = await _find_station_osm(lat, lon, tesla_only=tesla_only)
    if station_osm:
        return _osm_station_info(station_osm)
    if tesla_only:
        return "Tesla Supercharger (non localisé)", "Tesla Supercharger"
    return "", ""


# ── Logique principale ────────────────────────────────────────────────────────

async def plan_charging(req: ChargingRequest) -> ChargingPlanResponse:
    if req.vehicle_type == VehicleType.THERMAL:
        return ChargingPlanResponse(
            vehicle_type=VehicleType.THERMAL,
            fuel_or_charge_stops=[],
            feasible=True,
            warnings=["Véhicule thermique : aucun arrêt carburant imposé par le planificateur."],
        )

    battery = req.battery_capacity_kwh or DEFAULT_BATTERY_KWH
    consumption = req.consumption_kwh_per_100km or DEFAULT_CONSUMPTION_KWH_100KM
    warnings: list[str] = []

    if not req.battery_capacity_kwh or not req.consumption_kwh_per_100km:
        warnings.append(
            f"Paramètres batterie non fournis → hypothèses : {battery:.0f} kWh, "
            f"{consumption:.0f} kWh/100 km. Utilisez --battery-kwh et --consumption pour personnaliser."
        )

    # ── Phase 0 : facteurs d'élévation par segment (parallèle) ───────────────

    async def _elev_factor(seg) -> float:
        if seg.start_lat is None or seg.end_lat is None or seg.distance_km <= 0:
            return 1.0
        gain, loss = await elevation_gain_loss(
            (seg.start_lat, seg.start_lon),
            (seg.end_lat, seg.end_lon),
        )
        return consumption_factor(gain, loss, seg.distance_km, consumption)

    elevation_factors = await asyncio.gather(*[_elev_factor(seg) for seg in req.segments])

    for seg, ef in zip(req.segments, elevation_factors):
        if abs(ef - 1.0) >= 0.05:
            direction = "augmentée" if ef > 1.0 else "réduite"
            warnings.append(
                f"Jour {seg.day_index} : dénivelé → consommation {direction} de {(ef - 1) * 100:+.0f}%"
            )

    # ── Phase 1 : calcul des arrêts (pas d'IO) ───────────────────────────────
    stop_params: list[dict] = []
    charge_percent = req.current_charge_percent
    tesla_only = req.tesla_supercharger_only

    for seg, elev_factor in zip(req.segments, elevation_factors):
        range_km_seg = battery / (consumption * elev_factor) * 100.0
        remaining_km = seg.distance_km
        segment_progress_km = 0.0

        while True:
            available_km = (
                (charge_percent - req.min_arrival_charge_percent) / 100.0
            ) * range_km_seg

            if available_km >= remaining_km:
                charge_percent -= (remaining_km / range_km_seg) * 100.0
                break

            if available_km <= 0:
                warnings.append(
                    f"Jour {seg.day_index} : autonomie insuffisante en début de segment."
                )
                available_km = max(available_km, 10.0)

            drive_km = available_km
            segment_progress_km += drive_km
            remaining_km -= drive_km
            charge_percent -= (drive_km / range_km_seg) * 100.0

            charge_to = req.max_charge_stop_percent
            delta_percent = max(charge_to - charge_percent, 0.0)
            estimated_minutes = round(delta_percent / 100.0 * 35.0 + 5.0, 0)

            has_coords = (
                seg.start_lat is not None
                and seg.end_lat is not None
                and seg.distance_km > 0
            )
            if has_coords:
                frac = min(segment_progress_km / seg.distance_km, 1.0)
                lat = seg.start_lat + frac * (seg.end_lat - seg.start_lat)  # type: ignore[operator]
                lon = seg.start_lon + frac * (seg.end_lon - seg.start_lon)  # type: ignore[operator]
            else:
                lat, lon = 0.0, 0.0

            stop_params.append({
                "day_index": seg.day_index,
                "default_hint": f"~{round(segment_progress_km)} km après {seg.start_location}",
                "distance_from_day_start_km": round(segment_progress_km, 1),
                "charge_from_percent": round(charge_percent, 1),
                "charge_to_percent": charge_to,
                "estimated_charging_minutes": estimated_minutes,
                "lat": lat,
                "lon": lon,
                "has_coords": has_coords,
            })
            charge_percent = charge_to

    if not stop_params:
        return ChargingPlanResponse(
            vehicle_type=VehicleType.ELECTRIC,
            fuel_or_charge_stops=[],
            feasible=True,
            warnings=warnings,
        )

    # ── Phase 2 : lookups de bornes en parallèle ─────────────────────────────
    async def _no_coords() -> tuple[str, str]:
        return "", ""

    enrichments = await asyncio.gather(*[
        _enrich_stop(p["lat"], p["lon"], tesla_only) if p["has_coords"] else _no_coords()
        for p in stop_params
    ])

    # ── Phase 3 : assemblage ──────────────────────────────────────────────────
    stops = []
    for params, (loc, prov) in zip(stop_params, enrichments):
        stops.append(ChargingStop(
            day_index=params["day_index"],
            location_hint=loc if loc else params["default_hint"],
            distance_from_day_start_km=params["distance_from_day_start_km"],
            charge_from_percent=params["charge_from_percent"],
            charge_to_percent=params["charge_to_percent"],
            estimated_charging_minutes=params["estimated_charging_minutes"],
            station_provider_hint=prov if prov else None,
        ))

    return ChargingPlanResponse(
        vehicle_type=VehicleType.ELECTRIC,
        fuel_or_charge_stops=stops,
        feasible=True,
        warnings=warnings,
    )
