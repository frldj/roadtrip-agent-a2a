from __future__ import annotations

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from accommodation_agent.agent_executor import AccommodationAgentExecutor

import os

from dotenv import load_dotenv
load_dotenv()

HOST = "0.0.0.0"
PORT = int(os.getenv("ACCOMMODATION_AGENT_PORT", "9003"))


def build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="find_accommodation",
        name="Trouver des hébergements pour un roadtrip",
        description=(
            "Find hotels or campsites at each overnight stop along the route "
            "using OpenStreetMap (free, no API key required). "
            "Call after plan_route."
            "\n---\n"
            '{"type":"object","required":[],'
            '"properties":{'
            '"accommodation_type":{"type":"string",'
            '"enum":["hotel","camping","no_preference"],'
            '"description":"Preferred accommodation type (default: no_preference)"},'
            '"budget_per_night_eur":{"type":"number",'
            '"description":"Maximum budget per night in euros (optional)"}'
            "}}"
        ),
        tags=["roadtrip", "accommodation", "hotel", "camping", "osm"],
        examples=[
            '{"accommodation_type": "camping", "budget_per_night_eur": 30}'
        ],
        input_modes=["text"],
        output_modes=["text"],
    )
    return AgentCard(
        name="Accommodation Agent",
        description=(
            "Agent A2A spécialisé dans la recherche d'hébergements (hôtels, campings) "
            "aux étapes d'un roadtrip, via OpenStreetMap."
        ),
        url=f"http://localhost:{PORT}/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main() -> None:
    request_handler = DefaultRequestHandler(
        agent_executor=AccommodationAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )
    uvicorn.run(app.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
