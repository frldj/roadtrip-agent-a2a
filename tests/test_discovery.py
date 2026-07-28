"""Tests for A2A agent discovery and schema parsing."""
import json

import httpx
import pytest
import respx

from orchestrator.discovery import (
    DiscoveredAgent,
    _fetch_agent,
    _parse_skill_description,
    agent_to_tool,
    discover_agents,
)

_AGENT_CARD = {
    "name": "Route Agent",
    "description": "Routing agent",
    "url": "http://localhost:9001/",
    "version": "0.1.0",
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "capabilities": {"streaming": False},
    "skills": [
        {
            "id": "plan_route",
            "name": "Plan a route",
            "description": (
                "Compute a road-trip itinerary.\n---\n"
                '{"type":"object","required":["origin","destination"],'
                '"properties":{"origin":{"type":"string"},"destination":{"type":"string"}}}'
            ),
            "tags": ["routing"],
        }
    ],
}

_WELL_KNOWN_URL = "http://localhost:9001/.well-known/agent.json"


class TestParseSkillDescription:
    def test_splits_on_delimiter(self):
        raw = "Do something.\n---\n{\"type\": \"object\", \"properties\": {}}"
        human, schema = _parse_skill_description(raw)
        assert human == "Do something."
        assert schema == {"type": "object", "properties": {}}

    def test_no_delimiter_returns_empty_schema(self):
        human, schema = _parse_skill_description("Just a description")
        assert human == "Just a description"
        assert schema == {"type": "object", "properties": {}, "required": []}

    def test_invalid_json_falls_back(self):
        raw = "Description.\n---\n{not valid json}"
        human, schema = _parse_skill_description(raw)
        assert human == "Description."
        assert schema["properties"] == {}

    def test_preserves_required_fields(self):
        schema_str = '{"type":"object","required":["origin"],"properties":{"origin":{"type":"string"}}}'
        _, schema = _parse_skill_description(f"Desc.\n---\n{schema_str}")
        assert schema["required"] == ["origin"]
        assert "origin" in schema["properties"]


class TestAgentToTool:
    def test_converts_to_tool_definition(self):
        agent = DiscoveredAgent(
            url="http://localhost:9001",
            skill_id="plan_route",
            name="Route Agent",
            description="Compute a route",
            parameters={"type": "object", "properties": {"origin": {"type": "string"}}},
        )
        tool = agent_to_tool(agent)
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "plan_route"
        assert tool["function"]["description"] == "Compute a route"
        assert "origin" in tool["function"]["parameters"]["properties"]

    def test_skill_id_becomes_function_name(self):
        agent = DiscoveredAgent(
            url="http://x", skill_id="find_accommodation",
            name="Accom", description="Find hotels",
            parameters={"type": "object", "properties": {}},
        )
        assert agent_to_tool(agent)["function"]["name"] == "find_accommodation"


class TestFetchAgent:
    async def test_returns_discovered_agent_on_success(self):
        with respx.mock:
            respx.get(_WELL_KNOWN_URL).mock(
                return_value=httpx.Response(200, json=_AGENT_CARD)
            )
            agent = await _fetch_agent("http://localhost:9001")

        assert agent is not None
        assert agent.skill_id == "plan_route"
        assert agent.url == "http://localhost:9001"
        assert "origin" in agent.parameters["properties"]

    async def test_returns_none_on_connection_error(self):
        with respx.mock:
            respx.get(_WELL_KNOWN_URL).mock(side_effect=httpx.ConnectError("refused"))
            agent = await _fetch_agent("http://localhost:9001")

        assert agent is None

    async def test_returns_none_on_404(self):
        with respx.mock:
            respx.get(_WELL_KNOWN_URL).mock(return_value=httpx.Response(404))
            agent = await _fetch_agent("http://localhost:9001")

        assert agent is None

    async def test_description_parsed_into_human_and_schema(self):
        with respx.mock:
            respx.get(_WELL_KNOWN_URL).mock(
                return_value=httpx.Response(200, json=_AGENT_CARD)
            )
            agent = await _fetch_agent("http://localhost:9001")

        assert agent is not None
        assert "Compute" in agent.description
        assert "---" not in agent.description
        assert agent.parameters["required"] == ["origin", "destination"]


class TestDiscoverAgents:
    async def test_discovers_reachable_agents(self):
        with respx.mock:
            respx.get(_WELL_KNOWN_URL).mock(
                return_value=httpx.Response(200, json=_AGENT_CARD)
            )
            agents = await discover_agents(["http://localhost:9001"])

        assert len(agents) == 1
        assert agents[0].skill_id == "plan_route"

    async def test_skips_unreachable_agents(self):
        with respx.mock:
            respx.get(_WELL_KNOWN_URL).mock(side_effect=httpx.ConnectError("refused"))
            agents = await discover_agents(["http://localhost:9001"])

        assert agents == []

    async def test_partial_availability(self):
        card9002 = {**_AGENT_CARD, "url": "http://localhost:9002/",
                    "skills": [{**_AGENT_CARD["skills"][0], "id": "plan_charging",
                                "name": "Charging"}]}
        with respx.mock:
            respx.get(_WELL_KNOWN_URL).mock(side_effect=httpx.ConnectError("refused"))
            respx.get("http://localhost:9002/.well-known/agent.json").mock(
                return_value=httpx.Response(200, json=card9002)
            )
            agents = await discover_agents([
                "http://localhost:9001",
                "http://localhost:9002",
            ])

        assert len(agents) == 1
        assert agents[0].skill_id == "plan_charging"

    async def test_empty_url_list_returns_empty(self):
        agents = await discover_agents([])
        assert agents == []
