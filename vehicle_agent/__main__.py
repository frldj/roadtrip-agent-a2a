from __future__ import annotations

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from vehicle_agent.agent_executor import VehicleAgentExecutor

import os

from dotenv import load_dotenv
load_dotenv()

HOST = "0.0.0.0"
PORT = int(os.getenv("VEHICLE_AGENT_PORT", "9002"))


def build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="plan_charging",
        name="Planifier les arrêts recharge / carburant",
        description=(
            "Find charging stops (electric vehicle) or fuel stations (thermal) "
            "along the route. Call after plan_route. Works for both vehicle types. "
            "Also corrects energy consumption for elevation using SRTM data."
            "\n---\n"
            '{"type":"object","required":["vehicle_type"],'
            '"properties":{'
            '"vehicle_type":{"type":"string","enum":["electric","thermal"],'
            '"description":"Vehicle propulsion type"},'
            '"battery_capacity_kwh":{"type":"number",'
            '"description":"Battery capacity in kWh (electric only)"},'
            '"consumption_kwh_per_100km":{"type":"number",'
            '"description":"Energy consumption in kWh/100km (electric only)"},'
            '"tesla_supercharger_only":{"type":"boolean",'
            '"description":"Restrict to Tesla Superchargers only (default false)"}'
            "}}"
        ),
        tags=["roadtrip", "charging", "electric", "range", "ev"],
        examples=[
            '{"vehicle_type": "electric", "battery_capacity_kwh": 60, '
            '"consumption_kwh_per_100km": 17, "tesla_supercharger_only": false}'
        ],
        input_modes=["text"],
        output_modes=["text"],
    )
    return AgentCard(
        name="Vehicle Agent",
        description=(
            "Agent A2A spécialisé dans la gestion de l'autonomie véhicule "
            "(recharge électrique ou ravitaillement thermique)."
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
        agent_executor=VehicleAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )
    uvicorn.run(app.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
