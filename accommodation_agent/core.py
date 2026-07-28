"""
Logique métier de l'agent Hébergement.

Sources de données (par ordre de priorité) :
1. Google Places API (si GOOGLE_PLACES_KEY dans .env) — données riches, ratings réels,
   niveaux de prix, photos. Quota gratuit : $200/mois de crédit.
   Clé gratuite sur : https://console.cloud.google.com/
2. Overpass / OpenStreetMap (fallback, toujours gratuit, sans clé).
"""
from __future__ import annotations

import asyncio
import os

import httpx

from common.geocoding import geocode
from common.schemas import (
    AccommodationOption,
    AccommodationPlanResponse,
    AccommodationRequest,
    AccommodationType,
)

# ── Overpass ──────────────────────────────────────────────────────────────────

_OVERPASS_MIRRORS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
_OVERPASS_HEADERS = {
    "User-Agent": "roadtrip-a2a-demo/0.1 (contact: feriel.oulhadj@gmail.com)",
}
_HOTEL_FILTER = r"^(hotel|hostel|guest_house|motel|apartment)$"
_CAMPING_FILTER = r"^(camp_site)$"


async def _query_overpass(
    lat: float, lon: float, tourism_filter: str, radius_m: int, limit: int
) -> list[dict]:
    query = (
        f"[out:json][timeout:12];"
        f"("
        f'node["tourism"~"{tourism_filter}"]["name"](around:{radius_m},{lat},{lon});'
        f'way["tourism"~"{tourism_filter}"]["name"](around:{radius_m},{lat},{lon});'
        f");"
        f"out center {limit};"
    )
    last_exc: Exception | None = None
    for mirror in _OVERPASS_MIRRORS:
        try:
            async with httpx.AsyncClient(timeout=18, headers=_OVERPASS_HEADERS) as client:
                resp = await client.post(mirror, data={"data": query})
                resp.raise_for_status()
                return resp.json().get("elements", [])
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Overpass indisponible : {last_exc}")


def _estimate_price(tourism_type: str, stars: float | None) -> tuple[float, float] | None:
    if tourism_type == "camp_site":
        return (10.0, 30.0)
    if tourism_type == "hostel":
        return (20.0, 50.0)
    if tourism_type == "guest_house":
        return (45.0, 90.0)
    if tourism_type == "apartment":
        return (60.0, 120.0)
    if tourism_type in ("hotel", "motel"):
        if stars:
            if stars >= 5:
                return (200.0, 400.0)
            if stars >= 4:
                return (110.0, 220.0)
            if stars >= 3:
                return (70.0, 130.0)
            if stars >= 2:
                return (50.0, 95.0)
            return (35.0, 70.0)
        return (60.0, 130.0)
    return None


def _overpass_element_to_option(el: dict, day_index: int) -> AccommodationOption:
    tags = el.get("tags", {})
    name = tags.get("name", "Inconnu")
    tourism_type = tags.get("tourism", "")
    atype = AccommodationType.CAMPING if tourism_type == "camp_site" else AccommodationType.HOTEL

    stars_raw = tags.get("stars") or tags.get("classification")
    try:
        rating = float(stars_raw) if stars_raw else None
    except ValueError:
        rating = None

    price_range = _estimate_price(tourism_type, rating)
    price_min = price_range[0] if price_range else None
    price_max = price_range[1] if price_range else None

    location_hint = next(
        (tags.get(k) for k in ("addr:city", "addr:town", "addr:village") if tags.get(k)),
        "",
    )
    return AccommodationOption(
        day_index=day_index, name=name, type=atype,
        location_hint=location_hint,
        estimated_price_eur=round((price_min + price_max) / 2) if price_range else None,
        price_min_eur=price_min, price_max_eur=price_max,
        rating=rating,
    )


# ── Google Places API (New) ───────────────────────────────────────────────────
# Endpoint : https://places.googleapis.com/v1/places:searchNearby
# Documentation : https://developers.google.com/maps/documentation/places/web-service/nearby-search

GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchNearby"
GOOGLE_PLACES_FIELD_MASK = (
    "places.displayName,places.rating,places.priceLevel,"
    "places.formattedAddress,places.shortFormattedAddress"
)

_PRICE_LEVEL_MAP = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}
_GP_HOTEL_PRICE: dict[int, tuple[float, float]] = {
    0: (30.0,  70.0),
    1: (50.0, 100.0),
    2: (90.0, 160.0),
    3: (160.0, 280.0),
    4: (280.0, 500.0),
}
_GP_CAMPING_PRICE: dict[int, tuple[float, float]] = {
    0: (5.0,  15.0),
    1: (10.0, 25.0),
    2: (20.0, 40.0),
    3: (35.0, 60.0),
    4: (50.0, 100.0),
}

# Types Places API (New) : hôtels et campings
_GP_HOTEL_TYPES = ["hotel", "motel", "hostel", "bed_and_breakfast", "resort_hotel"]
_GP_CAMPING_TYPES = ["campground", "rv_park"]


async def _query_google_places(
    lat: float, lon: float, included_types: list[str], radius_m: int, limit: int
) -> list[dict]:
    api_key = os.getenv("GOOGLE_PLACES_KEY", "").strip()
    if not api_key:
        return []
    body = {
        "includedTypes": included_types,
        "maxResultCount": min(limit, 20),
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": float(radius_m),
            }
        },
        "languageCode": "fr",
    }
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": GOOGLE_PLACES_FIELD_MASK,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=12) as client:
        resp = await client.post(GOOGLE_PLACES_URL, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Google Places (New): {data['error'].get('message', data['error'])}")
        return data.get("places", [])[:limit]


def _gp_result_to_option(result: dict, day_index: int, is_camping: bool) -> AccommodationOption:
    name = (result.get("displayName") or {}).get("text", "Inconnu")
    rating = result.get("rating")  # float 0-5

    price_label = result.get("priceLevel", "")
    price_level = _PRICE_LEVEL_MAP.get(price_label)

    address = (
        result.get("shortFormattedAddress")
        or result.get("formattedAddress", "")
    )

    atype = AccommodationType.CAMPING if is_camping else AccommodationType.HOTEL
    price_table = _GP_CAMPING_PRICE if is_camping else _GP_HOTEL_PRICE
    price_range = price_table.get(price_level) if price_level is not None else None
    price_min = price_range[0] if price_range else None
    price_max = price_range[1] if price_range else None

    return AccommodationOption(
        day_index=day_index, name=name, type=atype,
        location_hint=address,
        estimated_price_eur=round((price_min + price_max) / 2) if price_range else None,
        price_min_eur=price_min, price_max_eur=price_max,
        rating=rating,
    )


# ── Logique principale ────────────────────────────────────────────────────────

async def _fetch_for_segment(
    lat: float, lon: float, day_index: int, accom_type: AccommodationType
) -> list[AccommodationOption]:
    """Cherche les hébergements pour un segment. Google Places > Overpass."""
    has_google_key = bool(os.getenv("GOOGLE_PLACES_KEY", "").strip())

    if has_google_key:
        return await _fetch_google(lat, lon, day_index, accom_type)

    return await _fetch_overpass(lat, lon, day_index, accom_type)


async def _fetch_google(
    lat: float, lon: float, day_index: int, accom_type: AccommodationType
) -> list[AccommodationOption]:
    try:
        if accom_type == AccommodationType.HOTEL:
            results = await _query_google_places(lat, lon, _GP_HOTEL_TYPES, 12_000, 5)
            return [_gp_result_to_option(r, day_index, False) for r in results]

        if accom_type == AccommodationType.CAMPING:
            results = await _query_google_places(lat, lon, _GP_CAMPING_TYPES, 25_000, 5)
            return [_gp_result_to_option(r, day_index, True) for r in results]

        # no_preference : hôtels + campings en parallèle
        hotels_task = _query_google_places(lat, lon, _GP_HOTEL_TYPES, 12_000, 3)
        camps_task = _query_google_places(lat, lon, _GP_CAMPING_TYPES, 25_000, 2)
        hotels_raw, camps_raw = await asyncio.gather(hotels_task, camps_task)
        options = [_gp_result_to_option(r, day_index, False) for r in hotels_raw]
        options += [_gp_result_to_option(r, day_index, True) for r in camps_raw]
        return options
    except Exception:
        return await _fetch_overpass(lat, lon, day_index, accom_type)


async def _fetch_overpass(
    lat: float, lon: float, day_index: int, accom_type: AccommodationType
) -> list[AccommodationOption]:
    try:
        if accom_type == AccommodationType.HOTEL:
            elements = await _query_overpass(lat, lon, _HOTEL_FILTER, 12_000, 5)
            return [_overpass_element_to_option(e, day_index) for e in elements]

        if accom_type == AccommodationType.CAMPING:
            elements = await _query_overpass(lat, lon, _CAMPING_FILTER, 25_000, 5)
            return [_overpass_element_to_option(e, day_index) for e in elements]

        hotels_task = _query_overpass(lat, lon, _HOTEL_FILTER, 12_000, 3)
        camps_task = _query_overpass(lat, lon, _CAMPING_FILTER, 25_000, 2)
        hotels_raw, camps_raw = await asyncio.gather(hotels_task, camps_task)
        options = [_overpass_element_to_option(e, day_index) for e in hotels_raw]
        options += [_overpass_element_to_option(e, day_index) for e in camps_raw]
        return options
    except Exception:
        return []


async def plan_accommodation(req: AccommodationRequest) -> AccommodationPlanResponse:
    # Résoudre les coordonnées de tous les segments
    coord_tasks = []
    valid_segs = []
    for seg in req.segments:
        if seg.end_lat is not None and seg.end_lon is not None:
            coord_tasks.append(asyncio.sleep(0, result=(seg.end_lat, seg.end_lon)))
            valid_segs.append(seg)
        else:
            coord_tasks.append(geocode(seg.end_location))
            valid_segs.append(seg)

    coords_results = await asyncio.gather(*coord_tasks, return_exceptions=True)

    # Lancer tous les lookups en parallèle
    fetch_tasks = []
    fetch_segs = []
    for seg, coords in zip(valid_segs, coords_results):
        if isinstance(coords, Exception):
            continue
        if seg.end_lat is None:
            lat, lon = coords  # type: ignore[misc]
        else:
            lat, lon = coords  # type: ignore[misc]
        fetch_tasks.append(_fetch_for_segment(lat, lon, seg.day_index, req.accommodation_type))
        fetch_segs.append(seg)

    all_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    all_options: list[AccommodationOption] = []
    for result in all_results:
        if isinstance(result, list):
            all_options.extend(result)

    return AccommodationPlanResponse(options=all_options)
