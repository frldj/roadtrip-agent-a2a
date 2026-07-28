"""
Pipeline déterministe original (hardcodé route→vehicle→accommodation).
Conservé comme fallback si le LLM ne supporte pas bien le tool-calling.
Utilisation : dans core.py, remplacer l'import par :
    from orchestrator._pipeline_core import plan_roadtrip
"""
from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

from common.a2a_client_utils import call_agent
from common.schemas import (
    AccommodationPlanResponse,
    AccommodationRequest,
    ChargingPlanResponse,
    ChargingRequest,
    RoadtripPlan,
    RoadtripRequest,
    RoutePlanRequest,
    RoutePlanResponse,
)

ROUTE_AGENT_URL = os.getenv("ROUTE_AGENT_URL", "http://localhost:9001")
VEHICLE_AGENT_URL = os.getenv("VEHICLE_AGENT_URL", "http://localhost:9002")
ACCOMMODATION_AGENT_URL = os.getenv("ACCOMMODATION_AGENT_URL", "http://localhost:9003")

ProgressCallback = Callable[[str], Awaitable[None]]

try:
    from monitoring.metrics import (
        agent_call_duration,
        agent_calls,
        planning_duration,
        planning_requests,
    )
    _METRICS = True
except Exception:
    _METRICS = False


async def _timed_agent_call(agent_name: str, url: str, payload: dict) -> dict:
    t = time.perf_counter()
    status = "success"
    try:
        result = await call_agent(url, payload)
        if "error" in result:
            status = "error"
        return result
    except Exception:
        status = "error"
        raise
    finally:
        if _METRICS:
            elapsed = time.perf_counter() - t
            agent_call_duration.labels(agent=agent_name, status=status).observe(elapsed)
            agent_calls.labels(agent=agent_name, status=status).inc()


async def plan_roadtrip(
    req: RoadtripRequest,
    on_progress: ProgressCallback | None = None,
) -> RoadtripPlan:
    async def _progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    t_plan = time.perf_counter()
    plan_status = "success"

    try:
        await _progress(f"Calcul de l'itinéraire {req.origin} → {req.destination}…")
        route_req = RoutePlanRequest(
            origin=req.origin,
            destination=req.destination,
            waypoints=req.waypoints,
            max_driving_hours_per_day=req.max_driving_hours_per_day,
            start_date=req.start_date,
        )
        route_raw = await _timed_agent_call("route", ROUTE_AGENT_URL, route_req.model_dump())
        if "error" in route_raw:
            raise RuntimeError(f"route_agent error: {route_raw['error']}")
        route = RoutePlanResponse.model_validate(route_raw)

        await _progress("Planification de la recharge et calcul du dénivelé…")
        charging_req = ChargingRequest(
            vehicle_type=req.vehicle_type,
            segments=route.segments,
            battery_capacity_kwh=req.battery_capacity_kwh,
            consumption_kwh_per_100km=req.consumption_kwh_per_100km,
            tesla_supercharger_only=req.tesla_supercharger_only,
        )
        charging: ChargingPlanResponse | None
        charging_raw = await _timed_agent_call(
            "vehicle", VEHICLE_AGENT_URL, charging_req.model_dump()
        )
        charging = None if "error" in charging_raw else ChargingPlanResponse.model_validate(charging_raw)

        await _progress("Recherche d'hébergements…")
        accommodation: AccommodationPlanResponse | None = None
        accom_req = AccommodationRequest(
            segments=route.segments,
            accommodation_type=req.accommodation_type,
            budget_per_night_eur=req.budget_per_night_eur,
        )
        try:
            accom_raw = await _timed_agent_call(
                "accommodation", ACCOMMODATION_AGENT_URL, accom_req.model_dump()
            )
            if "error" not in accom_raw:
                accommodation = AccommodationPlanResponse.model_validate(accom_raw)
        except Exception:
            pass

        return RoadtripPlan(route=route, charging=charging, accommodation=accommodation)

    except Exception:
        plan_status = "error"
        raise
    finally:
        if _METRICS:
            elapsed = time.perf_counter() - t_plan
            planning_duration.labels(status=plan_status).observe(elapsed)
            planning_requests.labels(status=plan_status).inc()
