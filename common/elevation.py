"""Profil altimétrique via OpenTopoData (SRTM 90m, gratuit, sans clé)."""
from __future__ import annotations

import httpx

_OPENTOPO_URL = "https://api.opentopodata.org/v1/srtm90m"
_HEADERS = {"User-Agent": "roadtrip-a2a-demo/0.1 (contact: feriel.oulhadj@gmail.com)"}
_cache: dict[str, tuple[float, float]] = {}

# Modèle physique EV (1800 kg, rendement moteur 90 %, récupération 70 %)
_MASS_KG = 1800.0
_G = 9.81
_MOTOR_EFF = 0.9
_REGEN_EFF = 0.7
_J_TO_KWH = 1.0 / 3_600_000.0


async def elevation_gain_loss(
    start: tuple[float, float],
    end: tuple[float, float],
    n_samples: int = 8,
) -> tuple[float, float]:
    """
    Retourne (gain_m, loss_m) en interpolant n_samples points le long du segment.
    Résultat mis en cache ; retourne (0, 0) si l'API est indisponible.
    """
    key = f"{start[0]:.4f},{start[1]:.4f}|{end[0]:.4f},{end[1]:.4f}|{n_samples}"
    if key in _cache:
        return _cache[key]

    fracs = [i / (n_samples - 1) for i in range(n_samples)]
    points = [
        (start[0] + f * (end[0] - start[0]), start[1] + f * (end[1] - start[1]))
        for f in fracs
    ]
    locations = "|".join(f"{lat},{lon}" for lat, lon in points)

    try:
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as client:
            resp = await client.get(_OPENTOPO_URL, params={"locations": locations})
            resp.raise_for_status()
            results = resp.json().get("results", [])
        elevs = [r.get("elevation") or 0.0 for r in results]
        gain = sum(max(elevs[i + 1] - elevs[i], 0.0) for i in range(len(elevs) - 1))
        loss = sum(max(elevs[i] - elevs[i + 1], 0.0) for i in range(len(elevs) - 1))
    except Exception:
        gain, loss = 0.0, 0.0

    _cache[key] = (gain, loss)
    return gain, loss


def consumption_factor(
    gain_m: float,
    loss_m: float,
    distance_km: float,
    consumption_kwh_per_100km: float,
) -> float:
    """
    Facteur multiplicatif sur la consommation, tenant compte du dénivelé.
    > 1.0 si montée nette, < 1.0 si descente nette (récupération partielle).
    Retourne 1.0 si les données sont nulles ou indisponibles.
    """
    if distance_km <= 0 or consumption_kwh_per_100km <= 0:
        return 1.0
    baseline_kwh = consumption_kwh_per_100km / 100.0 * distance_km
    extra_climb = gain_m * _MASS_KG * _G * _J_TO_KWH / _MOTOR_EFF
    saved_regen = loss_m * _MASS_KG * _G * _J_TO_KWH * _REGEN_EFF
    total_kwh = baseline_kwh + extra_climb - saved_regen
    return max(0.5, min(2.5, total_kwh / max(baseline_kwh, 1e-6)))
