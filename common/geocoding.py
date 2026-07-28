"""Géocodage partagé (Nominatim/OSM) avec cache en mémoire."""
from __future__ import annotations

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "roadtrip-a2a-demo/0.1 (contact: feriel.oulhadj@gmail.com)"}

_cache: dict[str, tuple[float, float]] = {}


async def geocode(place: str) -> tuple[float, float]:
    """
    Retourne (lat, lon) pour un nom de lieu, avec cache en mémoire.
    Accepte aussi le format "lat,lon" (coordonnées directes) — utilisé par
    vehicle_agent lorsqu'il transmet un point intermédiaire au route_agent.
    """
    if place in _cache:
        return _cache[place]
    # Format "lat,lon" : pas de requête Nominatim, retour direct
    parts = place.split(",")
    if len(parts) == 2:
        try:
            coords = float(parts[0].strip()), float(parts[1].strip())
            _cache[place] = coords
            return coords
        except ValueError:
            pass
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            NOMINATIM_URL,
            params={"q": place, "format": "json", "limit": 1},
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
    if not data:
        raise ValueError(f"Lieu introuvable : {place}")
    coords = float(data[0]["lat"]), float(data[0]["lon"])
    _cache[place] = coords
    return coords
