"""
LLM-driven orchestrator — ReAct loop (Reason + Act) with dynamic agent discovery.

At startup the orchestrator reads /.well-known/agent.json from every registered
URL, builds the LLM tool list from the discovered Agent Cards, and routes each
tool call back to the agent that owns that skill — without any hardcoded
knowledge of which agents exist or what they do.

Adding a new agent to the system:
  1. Add its URL to AGENT_REGISTRY_URLS (via env var or .env)
  2. The orchestrator discovers it automatically on next invocation.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from chat_client.ollama_client import ollama_chat
from common.a2a_client_utils import call_agent
from common.schemas import (
    AccommodationPlanResponse,
    AccommodationRequest,
    AccommodationType,
    ChargingPlanResponse,
    ChargingRequest,
    RoadtripPlan,
    RoadtripRequest,
    RoutePlanRequest,
    RoutePlanResponse,
)
from orchestrator.discovery import DiscoveredAgent, agent_to_tool, discover_agents

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")

# Registry: all agent URLs the orchestrator should probe at startup.
# Add new agents here (or override via env) — no other code changes needed.
AGENT_REGISTRY_URLS: list[str] = [
    os.getenv("ROUTE_AGENT_URL", "http://localhost:9001"),
    os.getenv("VEHICLE_AGENT_URL", "http://localhost:9002"),
    os.getenv("ACCOMMODATION_AGENT_URL", "http://localhost:9003"),
]

# Discovery cache — agents are re-discovered only when the cache expires.
# This avoids 3 extra HTTP round-trips on every plan request.
_DISCOVERY_CACHE: list[DiscoveredAgent] | None = None
_DISCOVERY_CACHE_EXPIRES: float = 0.0
_DISCOVERY_TTL = 300.0  # seconds — refresh every 5 min to pick up new/restarted agents


async def _get_agents() -> list[DiscoveredAgent]:
    global _DISCOVERY_CACHE, _DISCOVERY_CACHE_EXPIRES
    now = time.monotonic()
    if _DISCOVERY_CACHE is None or now >= _DISCOVERY_CACHE_EXPIRES:
        _DISCOVERY_CACHE = await discover_agents(AGENT_REGISTRY_URLS)
        _DISCOVERY_CACHE_EXPIRES = now + _DISCOVERY_TTL
    return _DISCOVERY_CACHE

ProgressCallback = Callable[[str], Awaitable[None]]

_SYSTEM_PROMPT = """\
You are a road-trip planning orchestrator. Use the available tools to build a complete trip plan.

Rules:
1. Call plan_route first — the other tools need its output.
2. Call plan_charging for every trip (electric and thermal vehicles both need stop information).
3. Call find_accommodation unless the user explicitly said no accommodation needed.
4. Call each tool at most once.
5. Make no text response — only tool calls. The system assembles the final plan.
"""

# ── Metrics (optional) ────────────────────────────────────────────────────────

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


async def _timed_agent_call(
    agent: DiscoveredAgent, payload: dict
) -> dict:
    t = time.perf_counter()
    status = "success"
    try:
        result = await call_agent(agent.url, payload, agent_card=agent.card)
        if "error" in result:
            status = "error"
        return result
    except Exception:
        status = "error"
        raise
    finally:
        if _METRICS:
            elapsed = time.perf_counter() - t
            agent_call_duration.labels(agent=agent.skill_id, status=status).observe(elapsed)
            agent_calls.labels(agent=agent.skill_id, status=status).inc()


# ── Payload builders ──────────────────────────────────────────────────────────
# The LLM provides user-facing parameters (vehicle type, budget, etc.).
# The orchestrator injects internal plumbing (route segments) that the LLM
# doesn't need to know about.

def _build_payload(
    skill_id: str,
    args: dict[str, Any],
    req: RoadtripRequest,
    route: RoutePlanResponse | None,
) -> dict[str, Any] | None:
    """
    Build the actual A2A payload for a given skill.
    Returns None (with an error dict) if prerequisites are missing.

    Known skills have dedicated builders that inject route segments.
    Unknown skills (new agents) receive the LLM's raw args directly —
    enabling zero-code extensibility for agents that are self-contained.
    """
    if skill_id == "plan_route":
        return RoutePlanRequest(
            origin=args.get("origin", req.origin),
            destination=args.get("destination", req.destination),
            waypoints=args.get("waypoints", req.waypoints),
            max_driving_hours_per_day=args.get(
                "max_driving_hours_per_day", req.max_driving_hours_per_day
            ),
            start_date=args.get("start_date", req.start_date),
        ).model_dump()

    if skill_id == "plan_charging":
        if route is None:
            return None
        return ChargingRequest(
            vehicle_type=args.get("vehicle_type", req.vehicle_type),
            segments=route.segments,
            battery_capacity_kwh=args.get("battery_capacity_kwh", req.battery_capacity_kwh),
            consumption_kwh_per_100km=args.get(
                "consumption_kwh_per_100km", req.consumption_kwh_per_100km
            ),
            tesla_supercharger_only=args.get(
                "tesla_supercharger_only", req.tesla_supercharger_only
            ),
        ).model_dump()

    if skill_id == "find_accommodation":
        if route is None:
            return None
        return AccommodationRequest(
            segments=route.segments,
            accommodation_type=AccommodationType(
                args.get("accommodation_type", req.accommodation_type.value)
            ),
            budget_per_night_eur=args.get("budget_per_night_eur", req.budget_per_night_eur),
        ).model_dump()

    # Unknown agent — pass LLM args directly (zero-code extensibility)
    return args


# ── Main entry point ──────────────────────────────────────────────────────────

async def plan_roadtrip(
    req: RoadtripRequest,
    on_progress: ProgressCallback | None = None,
) -> RoadtripPlan:
    """
    LLM-driven orchestration with dynamic agent discovery.

    1. Discover available agents from their Agent Cards.
    2. Build LLM tool list from discovered skills (no hardcoded TOOLS).
    3. Run ReAct loop: LLM picks tools → we execute → feed results back.
    4. Assemble and return RoadtripPlan.
    """
    async def _progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    t_plan = time.perf_counter()
    plan_status = "success"

    try:
        # ── 1. Discovery (cached — fetches cards only on first call or TTL expiry) ─
        agents: list[DiscoveredAgent] = await _get_agents()
        if not agents:
            raise RuntimeError(
                "No agents discovered. Make sure route_agent, vehicle_agent and "
                "accommodation_agent are running."
            )

        # Index by skill_id for O(1) routing of tool calls
        agent_by_skill: dict[str, DiscoveredAgent] = {a.skill_id: a for a in agents}
        tools = [agent_to_tool(a) for a in agents]

        # ── 2. ReAct loop ─────────────────────────────────────────────────────
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": req.model_dump_json()},
        ]

        route: RoutePlanResponse | None = None
        charging: ChargingPlanResponse | None = None
        accommodation: AccommodationPlanResponse | None = None

        # Enforce "call each tool at most once" at code level — the LLM may
        # ignore the system-prompt instruction, so we block duplicate calls here.
        called_tools: set[str] = set()

        for _step in range(8):  # safety cap — a complete plan needs ≤ 3 tool calls
            response = await ollama_chat(OLLAMA_MODEL, messages, tools=tools)
            llm_msg = response["message"]
            messages.append(llm_msg)

            tool_calls = llm_msg.get("tool_calls") or []
            if not tool_calls:
                break  # LLM is satisfied

            for tc in tool_calls:
                skill_id: str = tc["function"]["name"]
                args: dict = tc["function"]["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)

                if skill_id in called_tools:
                    messages.append({
                        "role": "tool",
                        "content": json.dumps({
                            "error": f"Tool '{skill_id}' was already called. "
                                     "Each tool must be called at most once. Stop calling tools."
                        }),
                    })
                    continue

                agent = agent_by_skill.get(skill_id)
                if agent is None:
                    # LLM hallucinated a tool that doesn't exist
                    messages.append({
                        "role": "tool",
                        "content": json.dumps({"error": f"unknown tool: {skill_id}"}),
                    })
                    continue

                called_tools.add(skill_id)
                await _progress(f"Calling {agent.name}…")

                payload = _build_payload(skill_id, args, req, route)
                if payload is None:
                    messages.append({
                        "role": "tool",
                        "content": json.dumps({"error": "plan_route must be called first"}),
                    })
                    continue

                raw = await _timed_agent_call(agent, payload)

                # Update internal state and build compact LLM-facing summary
                if skill_id == "plan_route" and "error" not in raw:
                    route = RoutePlanResponse.model_validate(raw)
                    tool_result: dict[str, Any] = {
                        "total_km": route.total_distance_km,
                        "days": len(route.segments),
                        "segments": [
                            {"day": s.day_index, "from": s.start_location,
                             "to": s.end_location, "km": round(s.distance_km)}
                            for s in route.segments
                        ],
                    }
                elif skill_id == "plan_charging" and "error" not in raw:
                    charging = ChargingPlanResponse.model_validate(raw)
                    tool_result = {
                        "stops": len(charging.fuel_or_charge_stops),
                        "feasible": charging.feasible,
                        "warnings": charging.warnings,
                    }
                elif skill_id == "find_accommodation" and "error" not in raw:
                    accommodation = AccommodationPlanResponse.model_validate(raw)
                    tool_result = {"options_found": len(accommodation.options)}
                else:
                    tool_result = raw  # error or unknown skill — pass through

                # Feed result back so the LLM can reason before deciding next step
                messages.append({"role": "tool", "content": json.dumps(tool_result)})

            # All available agents have been called — no need for another LLM turn
            if called_tools >= {a.skill_id for a in agents}:
                break

        if route is None:
            raise RuntimeError("LLM orchestrator did not call plan_route")

        return RoadtripPlan(route=route, charging=charging, accommodation=accommodation)

    except Exception:
        plan_status = "error"
        raise
    finally:
        if _METRICS:
            elapsed = time.perf_counter() - t_plan
            planning_duration.labels(status=plan_status).observe(elapsed)
            planning_requests.labels(status=plan_status).inc()
