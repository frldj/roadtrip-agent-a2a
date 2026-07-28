"""
Tests for the tool-call deduplication guard in the ReAct loop.

The LLM sometimes ignores the "call each tool at most once" system-prompt
instruction. The orchestrator enforces this at code level by tracking which
tools have been called and rejecting duplicate calls with an error message.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from common.schemas import (
    AccommodationPlanResponse,
    AccommodationType,
    ChargingPlanResponse,
    RoadtripRequest,
    RouteSegment,
    RoutePlanResponse,
    VehicleType,
)
from orchestrator.discovery import DiscoveredAgent


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _route_response() -> dict:
    return {
        "total_distance_km": 400.0,
        "total_duration_minutes": 240,
        "segments": [
            {
                "day_index": 1,
                "start_location": "Paris",
                "end_location": "Lyon",
                "distance_km": 400.0,
                "duration_minutes": 240,
                "start_lat": 48.85,
                "start_lon": 2.35,
                "end_lat": 45.75,
                "end_lon": 4.85,
                "path_hint": None,
            }
        ],
    }


def _charging_response() -> dict:
    return {
        "vehicle_type": "electric",
        "fuel_or_charge_stops": [],
        "feasible": True,
        "warnings": [],
    }


def _accommodation_response() -> dict:
    return {
        "options": [
            {
                "day_index": 1,
                "name": "Hotel Test",
                "type": "hotel",
                "location_hint": "Lyon",
                "estimated_price_eur": 80.0,
                "price_min_eur": None,
                "price_max_eur": None,
                "rating": None,
            }
        ]
    }


def _make_agents() -> list[DiscoveredAgent]:
    def _agent(skill_id: str, name: str) -> DiscoveredAgent:
        return DiscoveredAgent(
            url=f"http://localhost/fake/{skill_id}",
            skill_id=skill_id,
            name=name,
            description=f"Agent for {skill_id}",
            parameters={"type": "object", "properties": {}},
        )

    return [
        _agent("plan_route", "Route Agent"),
        _agent("plan_charging", "Vehicle Agent"),
        _agent("find_accommodation", "Accommodation Agent"),
    ]


def _req() -> RoadtripRequest:
    return RoadtripRequest(
        origin="Paris",
        destination="Lyon",
        vehicle_type=VehicleType.ELECTRIC,
        battery_capacity_kwh=60.0,
        consumption_kwh_per_100km=17.0,
        max_driving_hours_per_day=4.0,
        accommodation_type=AccommodationType.HOTEL,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestDedupGuard:
    """The orchestrator must block duplicate tool calls even when the LLM retries."""

    async def test_duplicate_tool_call_blocked(self):
        """
        LLM calls plan_route once (ok), then plan_charging twice.
        The second plan_charging call must be rejected — call_agent should
        only be invoked twice total (once per unique tool).
        """
        agents = _make_agents()

        # Turn 1: LLM calls plan_route
        # Turn 2: LLM calls plan_charging + find_accommodation
        # Turn 3: LLM re-calls plan_charging (duplicate — should be blocked)
        # Turn 4: LLM emits no tool_calls → loop ends
        llm_responses = [
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "plan_route", "arguments": {}}}
            ]}},
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "plan_charging", "arguments": {"vehicle_type": "electric"}}},
                {"function": {"name": "find_accommodation", "arguments": {}}},
            ]}},
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "plan_charging", "arguments": {"vehicle_type": "electric"}}},
            ]}},
            {"message": {"role": "assistant", "content": "Plan ready.", "tool_calls": []}},
        ]

        agent_responses = [
            _route_response(),
            _charging_response(),
            _accommodation_response(),
        ]

        with (
            patch("orchestrator.llm_core._get_agents", new=AsyncMock(return_value=agents)),
            patch("orchestrator.llm_core.ollama_chat", new=AsyncMock(side_effect=llm_responses)),
            patch("orchestrator.llm_core._timed_agent_call", new=AsyncMock(side_effect=agent_responses)) as mock_agent,
        ):
            from orchestrator.llm_core import plan_roadtrip
            plan = await plan_roadtrip(_req())

        # call_agent invoked exactly 3 times (route, charging, accommodation) — NOT 4
        assert mock_agent.call_count == 3
        assert plan.route is not None
        assert plan.charging is not None
        assert plan.accommodation is not None

    async def test_single_duplicate_does_not_corrupt_plan(self):
        """
        Even when the LLM retries plan_route after it already succeeded,
        the plan should still be valid with data from the first (successful) call.
        """
        agents = _make_agents()

        llm_responses = [
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "plan_route", "arguments": {}}}
            ]}},
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "plan_route", "arguments": {}}},  # duplicate
                {"function": {"name": "plan_charging", "arguments": {"vehicle_type": "electric"}}},
                {"function": {"name": "find_accommodation", "arguments": {}}},
            ]}},
            {"message": {"role": "assistant", "content": "Done.", "tool_calls": []}},
        ]

        agent_responses = [
            _route_response(),
            _charging_response(),
            _accommodation_response(),
        ]

        with (
            patch("orchestrator.llm_core._get_agents", new=AsyncMock(return_value=agents)),
            patch("orchestrator.llm_core.ollama_chat", new=AsyncMock(side_effect=llm_responses)),
            patch("orchestrator.llm_core._timed_agent_call", new=AsyncMock(side_effect=agent_responses)) as mock_agent,
        ):
            from orchestrator.llm_core import plan_roadtrip
            plan = await plan_roadtrip(_req())

        assert mock_agent.call_count == 3
        assert plan.route.total_distance_km == 400.0

    async def test_all_tools_called_once_exits_early(self):
        """
        Once every discovered tool has been called, the loop breaks immediately
        without waiting for the LLM to confirm — even if more LLM turns remain.
        """
        agents = _make_agents()

        # After the second LLM turn all 3 tools are done — the loop should exit
        # without consuming turn 3 from the mock (which would raise StopAsyncIteration
        # if accessed).
        llm_responses = [
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "plan_route", "arguments": {}}}
            ]}},
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "plan_charging", "arguments": {"vehicle_type": "electric"}}},
                {"function": {"name": "find_accommodation", "arguments": {}}},
            ]}},
            # This turn must NOT be consumed — if it is, the mock raises:
        ]

        agent_responses = [
            _route_response(),
            _charging_response(),
            _accommodation_response(),
        ]

        ollama_mock = AsyncMock(side_effect=llm_responses)

        with (
            patch("orchestrator.llm_core._get_agents", new=AsyncMock(return_value=agents)),
            patch("orchestrator.llm_core.ollama_chat", ollama_mock),
            patch("orchestrator.llm_core._timed_agent_call", new=AsyncMock(side_effect=agent_responses)),
        ):
            from orchestrator.llm_core import plan_roadtrip
            plan = await plan_roadtrip(_req())

        assert ollama_mock.call_count == 2  # only 2 LLM turns — early exit after all tools called
        assert plan.route is not None
