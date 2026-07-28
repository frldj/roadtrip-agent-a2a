from __future__ import annotations

import os

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv

from validator_agent.agent_executor import ValidatorAgentExecutor

load_dotenv()

HOST = "0.0.0.0"
PORT = int(os.getenv("VALIDATOR_AGENT_PORT", "9004"))


def build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="validate_plan",
        name="Valider la cohérence du plan de roadtrip",
        description=(
            "Vérifie la cohérence du plan assemblé : durée de conduite par jour, "
            "faisabilité de la recharge, couverture hébergement. "
            "Retourne une liste d'issues classées par sévérité (error/warning/info). "
            "Appeler après plan_route, plan_charging et find_accommodation."
            "\n---\n"
            '{"type":"object","required":["segments"],'
            '"properties":{'
            '"segments":{"type":"array","description":"Segments du trajet (de plan_route)"},'
            '"max_driving_hours_per_day":{"type":"number","description":"Max heures/jour"},'
            '"charging_feasible":{"type":"boolean","description":"Champ feasible de plan_charging"},'
            '"charging_warnings":{"type":"array","items":{"type":"string"}},'
            '"days_with_accommodation":{"type":"array","items":{"type":"integer"}},'
            '"days_needing_accommodation":{"type":"array","items":{"type":"integer"}}'
            "}}"
        ),
        tags=["roadtrip", "validation", "quality"],
        input_modes=["text"],
        output_modes=["text"],
    )
    return AgentCard(
        name="Validator Agent",
        description="Agent A2A de validation de plans de roadtrip.",
        url=f"http://localhost:{PORT}/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main() -> None:
    request_handler = DefaultRequestHandler(
        agent_executor=ValidatorAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )
    uvicorn.run(app.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
