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
            "Calcule un itinéraire routier entre une origine, une destination "
            "et des étapes optionnelles, puis le découpe en segments "
            "journaliers selon un nombre d'heures de conduite maximal."
        ),
        tags=["roadtrip", "itinéraire", "route", "navigation"],
        examples=[
            '{"origin": "Paris", "destination": "Barcelone", '
            '"max_driving_hours_per_day": 6}'
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
