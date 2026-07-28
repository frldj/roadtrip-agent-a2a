"""
Expose l'orchestrateur lui-même comme agent A2A (port 9000).

Permet à un client externe (UI de chat, autre agent) d'appeler l'orchestrateur
via le protocole A2A, sans passer par le CLI.

Lancement :
    python -m orchestrator.server
"""
from __future__ import annotations

import json
import logging

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import get_data_parts

from common.a2a_data_utils import new_agent_data_message, new_status_event
from common.schemas import RoadtripRequest
from orchestrator.core import plan_roadtrip

logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 9000


class OrchestratorAgentExecutor(AgentExecutor):
    """
    Reçoit un JSON `RoadtripRequest`, orchestre les agents spécialisés,
    et renvoie un JSON `RoadtripPlan`.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            data_parts = get_data_parts(context.message.parts)
            if data_parts:
                payload = data_parts[0]
            else:
                payload = json.loads(context.get_user_input())

            req = RoadtripRequest.model_validate(payload)

            async def _on_progress(msg: str) -> None:
                await event_queue.enqueue_event(
                    new_status_event(msg, context.context_id, context.task_id)
                )

            plan = await plan_roadtrip(req, on_progress=_on_progress)
            response = new_agent_data_message(
                plan.model_dump(), context_id=context.context_id
            )
        except Exception as exc:
            logger.exception("Erreur orchestrator")
            response = new_agent_data_message(
                {"error": str(exc)}, context_id=context.context_id
            )

        await event_queue.enqueue_event(response)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported for orchestrator")


def build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="plan_roadtrip",
        name="Planifier un roadtrip complet",
        description=(
            "Orchestre les agents Route, Véhicule et Hébergement pour produire "
            "un plan de roadtrip complet : itinéraire journalier, arrêts de recharge "
            "(électrique) et suggestions d'hébergement à chaque étape."
        ),
        tags=["roadtrip", "orchestrateur", "itinéraire", "recharge", "hébergement"],
        examples=[
            '{"origin": "Paris", "destination": "Barcelone", "waypoints": ["Lyon"], '
            '"vehicle_type": "electric", "battery_capacity_kwh": 60, '
            '"consumption_kwh_per_100km": 17, "max_driving_hours_per_day": 5}'
        ],
        input_modes=["text"],
        output_modes=["text"],
    )
    return AgentCard(
        name="Roadtrip Orchestrator",
        description=(
            "Agent A2A orchestrateur : planifie un roadtrip de bout en bout "
            "en coordonnant les agents Route, Véhicule et Hébergement."
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
        agent_executor=OrchestratorAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )
    uvicorn.run(app.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
