"""
Interface conversationnelle roadtrip — LLM local via Ollama.

Prérequis :
    ollama serve                    # dans un terminal dédié
    python -m route_agent &
    python -m vehicle_agent &
    python -m accommodation_agent &

Lancement :
    python -m chat_client
    python -m chat_client --model qwen2.5:7b   # forcer un modèle
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

from chat_client.ollama_client import chat_stream, list_models
from common.schemas import RoadtripRequest
from orchestrator.__main__ import format_plan
from orchestrator.core import plan_roadtrip

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

# ── Extraction JSON depuis la réponse LLM ─────────────────────────────────────

_PLAN_RE = re.compile(
    r"PLAN_READY:\s*```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL,
)
_PLAN_BARE_RE = re.compile(r"PLAN_READY:\s*(\{.*?\})", re.DOTALL)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_REQUIRED_FIELDS = {"origin", "destination", "vehicle_type"}


def _extract_plan(text: str) -> RoadtripRequest | None:
    # 1. Marqueur explicite PLAN_READY:
    for pattern in (_PLAN_RE, _PLAN_BARE_RE):
        m = pattern.search(text)
        if m:
            try:
                return RoadtripRequest.model_validate(json.loads(m.group(1)))
            except Exception:
                pass

    # 2. Fallback : bloc ```json``` avec les champs requis (LLM sans PLAN_READY:)
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


# ── Affichage ─────────────────────────────────────────────────────────────────

_SEP = "─" * 54


def _clear_line() -> None:
    print("\r" + " " * 60 + "\r", end="", flush=True)


def _banner(model: str) -> None:
    print()
    print("╔" + "═" * 54 + "╗")
    print(f"║  🗺  Assistant Roadtrip  ·  {model:<25}║")
    print("╚" + "═" * 54 + "╝")
    print("  Tapez votre demande en langage naturel.")
    print("  Ctrl+C ou 'quitter' pour sortir.")
    print()


# ── Boucle de conversation ────────────────────────────────────────────────────

async def conversation_loop(model: str) -> None:
    _banner(model)
    history: list[dict] = [{"role": "system", "content": _SYSTEM}]

    while True:
        # Saisie utilisateur
        try:
            user_input = input("Vous : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Au revoir !")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quitter", "quit", "exit", "q"}:
            print("  Au revoir !")
            break

        history.append({"role": "user", "content": user_input})

        # Réponse en streaming
        print("Assistant : ", end="", flush=True)
        full_response = ""
        try:
            async for token in chat_stream(model, history):
                # Masquer la balise PLAN_READY et le JSON dans le flux affiché
                if "PLAN_READY" in full_response + token:
                    idx = (full_response + token).find("PLAN_READY")
                    visible = (full_response + token)[:idx]
                    # Réafficher uniquement la partie visible
                    if visible and not full_response[:idx]:
                        print(visible, end="", flush=True)
                    full_response += token
                    continue
                print(token, end="", flush=True)
                full_response += token
        except RuntimeError as exc:
            print(f"\n  ✗  {exc}")
            break

        print()  # saut de ligne après la réponse

        # Ajouter la réponse à l'historique (sans afficher les balises internes)
        history.append({"role": "assistant", "content": full_response})

        # Détecter si le LLM a produit un plan
        req = _extract_plan(full_response)
        if req is None:
            continue

        # Planification
        print()
        print(_SEP)

        async def on_progress(msg: str) -> None:
            print(f"  ⏳ {msg}")

        try:
            plan = await plan_roadtrip(req, on_progress=on_progress)
        except Exception as exc:
            print(f"  ✗  Erreur lors de la planification : {exc}")
            print()
            print("  Vérifiez que les 3 agents tournent :")
            print("    python -m route_agent &")
            print("    python -m vehicle_agent &")
            print("    python -m accommodation_agent &")
            # Permettre de continuer la conversation
            history.append({
                "role": "user",
                "content": f"[SYSTÈME] Erreur lors de la planification : {exc}. "
                           "Informe l'utilisateur et propose de corriger les paramètres.",
            })
            continue

        print(_SEP)
        print()
        print(format_plan(plan, req))

        # Proposer un autre roadtrip via la conversation
        follow_up = (
            "Le plan a été généré et affiché à l'utilisateur. "
            "Demande-lui s'il veut ajuster quelque chose ou planifier un autre roadtrip."
        )
        history.append({"role": "user", "content": f"[SYSTÈME] {follow_up}"})

        print("Assistant : ", end="", flush=True)
        full_response = ""
        try:
            async for token in chat_stream(model, history):
                print(token, end="", flush=True)
                full_response += token
        except RuntimeError:
            pass
        print()
        history.append({"role": "assistant", "content": full_response})


# ── Point d'entrée ────────────────────────────────────────────────────────────

async def main_async() -> None:
    # Déterminer le modèle à utiliser
    model = os.getenv("OLLAMA_MODEL", "")
    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        if idx + 1 < len(sys.argv):
            model = sys.argv[idx + 1]

    if not model:
        models = await list_models()
        if not models:
            print("✗  Ollama n'est pas joignable ou aucun modèle installé.")
            print("   Lancez :  ollama serve")
            print("   Puis :    ollama pull qwen2.5")
            sys.exit(1)
        # Préférer qwen2.5:7b > qwen2.5 > tout autre modèle (le plus petit sinon)
        preferred = ["qwen2.5:7b", "qwen2.5:latest", "qwen2.5"]
        model = next((m for m in preferred if m in models), models[0])

    await conversation_loop(model)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
