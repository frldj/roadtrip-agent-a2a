from __future__ import annotations

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from route_agent.agent_executor import RouteAgentExecutor

import os

from dotenv import load_dotenv
load_dotenv()

HOST = "0.0.0.0"
PORT = int(os.getenv("ROUTE_AGENT_PORT", "9001"))


def build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="plan_route",
        name="Planifier un itinéraire de roadtrip",
        description=(
            "Compute a multi-day road-trip itinerary between origin and destination "
            "using real routing data (OSRM). Always call this first — the other agents "
            "need its route segments."
            "\n---\n"
            '{"type":"object","required":["origin","destination"],'
            '"properties":{'
            '"origin":{"type":"string","description":"Departure city or address"},'
            '"destination":{"type":"string","description":"Arrival city or address"},'
            '"waypoints":{"type":"array","items":{"type":"string"},'
            '"description":"Optional intermediate stops"},'
            '"max_driving_hours_per_day":{"type":"number",'
            '"description":"Max driving hours per day (default 6)"},'
            '"start_date":{"type":"string",'
            '"description":"Trip start date ISO e.g. 2026-08-10 (optional)"}'
            "}}"
        ),
        tags=["roadtrip", "routing", "osrm", "itinerary"],
        examples=[
            '{"origin": "Paris", "destination": "Barcelona", "max_driving_hours_per_day": 6}'
        ],
        input_modes=["text"],
        output_modes=["text"],
    )
    return AgentCard(
        name="Route Agent",
        description="Agent A2A spécialisé dans le calcul d'itinéraires routiers.",
        url=f"http://localhost:{PORT}/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main() -> None:
    request_handler = DefaultRequestHandler(
        agent_executor=RouteAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )
    uvicorn.run(app.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
