"""
Interface web pour l'assistant roadtrip.

Prérequis :
    ollama serve
    python -m route_agent &
    python -m vehicle_agent &
    python -m accommodation_agent &

Lancement :
    python -m web_server
    puis ouvrir http://localhost:8765
    métriques Prometheus : http://localhost:8765/metrics
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

load_dotenv()

from chat_client.ollama_client import chat_stream, list_models
from common.schemas import RoadtripRequest
from monitoring.metrics import (
    get_metrics_app,
    llm_conversation_turns,
    plan_extractions,
    ws_connections_active,
    ws_messages,
)
from orchestrator.core import plan_roadtrip

logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 8765
STATIC_DIR = pathlib.Path(__file__).parent / "static"

app = FastAPI(title="Roadtrip A2A Web")
app.mount("/metrics", get_metrics_app())

# ── Prompt système ────────────────────────────────────────────────────────────

_SYSTEM = """\
Tu es un assistant de planification de roadtrip. Tu converses naturellement \
pour collecter les paramètres du voyage, puis tu déclenches la planification.

PARAMÈTRES À COLLECTER (dans l'ordre logique, une ou deux questions à la fois) :
1. Ville de départ et destination  [obligatoire]
2. Étapes intermédiaires           [optionnel, liste ou vide]
3. Type de véhicule : "electric" ou "thermal"  [obligatoire]
4. Si électrique :
   - capacité batterie (kWh)         [défaut 60]
   - consommation (kWh/100 km)       [défaut 17]
   - Tesla Supercharger uniquement ? [true/false, défaut false]
5. Heures de conduite max par jour   [défaut 6]
6. Hébergement : "hotel", "camping" ou "no_preference"  [défaut no_preference]
7. Budget par nuit en €              [optionnel, null si absent]
8. Date de départ ISO YYYY-MM-DD     [optionnel, null si absente]

Quand tu as les informations essentielles (au minimum : départ, destination, \
type de véhicule) ET que l'utilisateur n'a plus de questions, génère le plan \
en terminant ta réponse par la balise suivante sur une ligne seule, \
immédiatement suivie d'un bloc JSON valide :

PLAN_READY:
```json
{"origin":"...","destination":"...","waypoints":[],"vehicle_type":"electric",\
"battery_capacity_kwh":60.0,"consumption_kwh_per_100km":17.0,\
"max_driving_hours_per_day":6.0,"accommodation_type":"no_preference",\
"budget_per_night_eur":null,"start_date":null,"tesla_supercharger_only":false}
```

Règles importantes :
- Sois bref et amical, pose au maximum 2 questions par réponse.
- Réponds dans la langue de l'utilisateur (français par défaut).
- Ne génère PLAN_READY que quand tu es prêt à planifier.
- Les valeurs manquantes restent null (jamais de chaîne vide pour les champs nullable).
"""

_PLAN_RE = re.compile(r"PLAN_READY:\s*```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_PLAN_BARE_RE = re.compile(r"PLAN_READY:\s*(\{.*?\})", re.DOTALL)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_REQUIRED_FIELDS = {"origin", "destination", "vehicle_type"}


def _extract_plan(text: str) -> RoadtripRequest | None:
    # 1. Chercher le marqueur explicite PLAN_READY: (format attendu)
    for pattern in (_PLAN_RE, _PLAN_BARE_RE):
        m = pattern.search(text)
        if m:
            try:
                return RoadtripRequest.model_validate(json.loads(m.group(1)))
            except Exception:
                pass

    # 2. Fallback : n'importe quel bloc ```json``` contenant les champs requis
    #    (le LLM peut omettre PLAN_READY: tout en produisant le bon JSON)
    for m in _JSON_BLOCK_RE.finditer(text):
        try:
            data = json.loads(m.group(1))
            if not _REQUIRED_FIELDS.issubset(data.keys()):
                continue
            if data.get("vehicle_type") not in ("electric", "thermal"):
                continue
            if not data.get("origin") or not data.get("destination"):
                continue
            return RoadtripRequest.model_validate(data)
        except Exception:
            continue

    return None


async def _get_model() -> str:
    model = os.getenv("OLLAMA_MODEL", "")
    if model:
        return model
    models = await list_models()
    if not models:
        return ""
    preferred = ["qwen2.5:7b", "qwen2.5:latest", "qwen2.5"]
    return next((m for m in preferred if m in models), models[0])


# ── Route HTTP ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ── WebSocket /ws/chat ────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    await ws.accept()
    ws_connections_active.inc()

    try:
        model = await _get_model()
        if not model:
            await ws.send_json({
                "type": "error",
                "content": "Ollama n'est pas joignable. Lancez : ollama serve",
            })
            await ws.close()
            return

        await ws.send_json({"type": "ready", "model": model})
        history: list[dict] = [{"role": "system", "content": _SYSTEM}]

        while True:
            # ── Réception message utilisateur ─────────────────────────────
            try:
                data = await ws.receive_json()
            except (WebSocketDisconnect, Exception):
                break

            if data.get("type") != "user_message":
                continue

            user_msg = str(data.get("content", "")).strip()
            if not user_msg:
                continue

            ws_messages.labels(direction="inbound").inc()
            llm_conversation_turns.labels(model=model).inc()
            history.append({"role": "user", "content": user_msg})

            # ── Streaming réponse LLM ─────────────────────────────────────
            full_response = ""
            plan_ready_pos = -1
            sent_up_to = 0

            try:
                async for token in chat_stream(model, history):
                    full_response += token

                    if plan_ready_pos == -1:
                        pr_idx = full_response.find("PLAN_READY")
                        if pr_idx == -1:
                            to_send = full_response[sent_up_to:]
                            if to_send:
                                await ws.send_json({"type": "token", "content": to_send})
                                sent_up_to = len(full_response)
                        else:
                            plan_ready_pos = pr_idx
                            visible = full_response[sent_up_to:pr_idx]
                            if visible:
                                await ws.send_json({"type": "token", "content": visible})
                            await ws.send_json({"type": "message_done"})
            except WebSocketDisconnect:
                break
            except RuntimeError as exc:
                await ws.send_json({"type": "error", "content": str(exc)})
                continue

            if plan_ready_pos == -1:
                await ws.send_json({"type": "message_done"})

            history.append({"role": "assistant", "content": full_response})

            # ── Détection et exécution du plan ────────────────────────────
            req = _extract_plan(full_response)
            if req is None:
                await ws.send_json({"type": "done"})
                continue

            plan_extractions.labels(status="success").inc()
            await ws.send_json({"type": "plan_start"})

            async def on_progress(msg: str) -> None:
                try:
                    await ws.send_json({"type": "progress", "content": msg})
                except Exception:
                    pass

            try:
                plan = await plan_roadtrip(req, on_progress=on_progress)
                plan_data = plan.model_dump(mode="json")
                await ws.send_json({"type": "plan", "data": plan_data})
                ws_messages.labels(direction="outbound").inc()
            except Exception as exc:
                logger.exception("Erreur planification")
                await ws.send_json({"type": "error", "content": f"Erreur : {exc}"})
                history.append({
                    "role": "user",
                    "content": (
                        f"[SYSTÈME] Erreur lors de la planification : {exc}. "
                        "Informe l'utilisateur et propose de corriger les paramètres."
                    ),
                })
                full_err = ""
                try:
                    async for token in chat_stream(model, history):
                        full_err += token
                        await ws.send_json({"type": "token", "content": token})
                except Exception:
                    pass
                await ws.send_json({"type": "message_done"})
                history.append({"role": "assistant", "content": full_err})
                await ws.send_json({"type": "done"})
                continue

            # ── Follow-up LLM après affichage du plan ─────────────────────
            history.append({
                "role": "user",
                "content": (
                    "[SYSTÈME] Le plan a été généré et affiché à l'utilisateur sur une carte "
                    "interactive. Il peut voir les arrêts de recharge et les hébergements sur la "
                    "carte. Demande-lui s'il veut ajuster quelque chose ou planifier un autre roadtrip."
                ),
            })

            full_follow = ""
            try:
                async for token in chat_stream(model, history):
                    full_follow += token
                    await ws.send_json({"type": "token", "content": token})
            except Exception:
                pass

            await ws.send_json({"type": "message_done"})
            history.append({"role": "assistant", "content": full_follow})
            await ws.send_json({"type": "done"})

    finally:
        ws_connections_active.dec()


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
