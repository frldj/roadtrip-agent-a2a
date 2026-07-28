"""
A2A agent discovery via Agent Cards (/.well-known/agent.json).

Each agent embeds its LLM-facing parameter schema in AgentSkill.description
after a '---' delimiter. This module reads those cards, parses the schemas,
and builds the tool definitions the LLM orchestrator needs — without any
hardcoded knowledge of which agents exist or what they do.

Adding a new agent to the system only requires adding its URL to
AGENT_REGISTRY_URLS; the orchestrator discovers everything else at runtime.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from a2a.client import A2ACardResolver

logger = logging.getLogger(__name__)

_SCHEMA_DELIMITER = "\n---\n"


@dataclass
class DiscoveredAgent:
    """Everything the orchestrator needs about one A2A agent."""

    url: str
    skill_id: str         # used as the tool function name in TOOLS
    name: str
    description: str      # human-readable (schema stripped out)
    parameters: dict[str, Any]  # JSON Schema for LLM tool-calling
    card: Any = None      # raw AgentCard — passed to call_agent to skip re-fetch


def _parse_skill_description(raw: str) -> tuple[str, dict[str, Any]]:
    """
    Split 'human text\\n---\\nJSON schema' into (text, schema).
    Falls back to an empty schema if the delimiter is absent (graceful degradation).
    """
    _empty_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    if _SCHEMA_DELIMITER in raw:
        human, _, schema_str = raw.partition(_SCHEMA_DELIMITER)
        try:
            return human.strip(), json.loads(schema_str.strip())
        except json.JSONDecodeError:
            logger.warning("Could not parse embedded schema from skill description")
            return human.strip(), _empty_schema
    return raw.strip(), _empty_schema


async def _fetch_agent(url: str) -> DiscoveredAgent | None:
    """
    Fetch one agent card from /.well-known/agent.json and extract its skill.
    Returns None (and logs a warning) if the agent is unreachable or has no skills.
    Unreachable agents are silently skipped — the LLM adapts to what's available.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resolver = A2ACardResolver(client, base_url=url)
            card = await resolver.get_agent_card()

        if not card.skills:
            logger.warning("Agent at %s responded but has no skills — skipping", url)
            return None

        skill = card.skills[0]  # one skill per agent in this project
        description, parameters = _parse_skill_description(skill.description)
        logger.info("Discovered agent '%s' (skill: %s) at %s", card.name, skill.id, url)
        return DiscoveredAgent(
            url=url,
            skill_id=skill.id,
            name=skill.name,
            description=description,
            parameters=parameters,
            card=card,
        )
    except Exception as exc:
        logger.warning("Agent at %s is unreachable (%s) — skipping", url, exc)
        return None


async def discover_agents(urls: list[str]) -> list[DiscoveredAgent]:
    """
    Probe all URLs concurrently and return only reachable agents.
    Order in the returned list matches the order tools will be presented to the LLM.
    """
    results = await asyncio.gather(*(_fetch_agent(u) for u in urls))
    found = [r for r in results if r is not None]
    if not found:
        logger.error("No agents discovered — check that agents are running")
    return found


def agent_to_tool(agent: DiscoveredAgent) -> dict[str, Any]:
    """Convert a DiscoveredAgent into an Ollama/OpenAI function-tool definition."""
    return {
        "type": "function",
        "function": {
            "name": agent.skill_id,
            "description": agent.description,
            "parameters": agent.parameters,
        },
    }
