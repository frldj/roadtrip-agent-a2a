"""
Métriques OpenTelemetry / Prometheus pour Roadtrip A2A.

Utilise prometheus_client (couche basse de l'exporteur OTEL Prometheus)
pour une nomenclature stable et prévisible.

Métriques exposées :
  LLM conversation  — durée, TTFT, tokens/s, taux d'erreur
  Planification     — durée totale, taux de succès, déclenchements PLAN_READY
  Agents A2A        — durée par agent, taux d'erreur
  WebSocket         — connexions actives, messages
  Cache             — hits / misses par type
"""
from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    make_asgi_app,
    REGISTRY,
)

# ── LLM conversation ──────────────────────────────────────────────────────────

llm_request_duration = Histogram(
    "roadtrip_llm_request_duration_seconds",
    "Durée totale d'une requête LLM (premier token → dernier token)",
    ["model", "status"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 120],
)

llm_ttft = Histogram(
    "roadtrip_llm_time_to_first_token_seconds",
    "Temps jusqu'au premier token (latence de démarrage du LLM)",
    ["model"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

llm_tokens_generated = Counter(
    "roadtrip_llm_tokens_generated_total",
    "Nombre de tokens générés par le LLM",
    ["model"],
)

llm_requests = Counter(
    "roadtrip_llm_requests_total",
    "Total des requêtes LLM",
    ["model", "status"],  # status: success | error
)

llm_generation_rate = Histogram(
    "roadtrip_llm_tokens_per_second",
    "Vitesse de génération du LLM (tokens / durée totale)",
    ["model"],
    buckets=[1, 5, 10, 20, 50, 100, 200],
)

llm_conversation_turns = Counter(
    "roadtrip_llm_conversation_turns_total",
    "Nombre de tours de conversation (échanges utilisateur → LLM)",
    ["model"],
)

# ── Planification ─────────────────────────────────────────────────────────────

plan_extractions = Counter(
    "roadtrip_plan_extractions_total",
    "Nombre de fois que PLAN_READY a été extrait de la réponse LLM",
    ["status"],  # status: success | parse_error
)

planning_duration = Histogram(
    "roadtrip_planning_duration_seconds",
    "Durée totale de la planification (route + recharge + hébergement)",
    ["status"],  # status: success | error
    buckets=[1, 2, 5, 10, 20, 30, 60, 120],
)

planning_requests = Counter(
    "roadtrip_planning_requests_total",
    "Total des demandes de planification de roadtrip",
    ["status"],
)

# ── Agents A2A ────────────────────────────────────────────────────────────────

agent_call_duration = Histogram(
    "roadtrip_agent_call_duration_seconds",
    "Durée d'un appel A2A par agent spécialisé",
    ["agent", "status"],  # agent: route | vehicle | accommodation
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20, 30, 60],
)

agent_calls = Counter(
    "roadtrip_agent_calls_total",
    "Total des appels A2A par agent",
    ["agent", "status"],
)

# ── WebSocket ─────────────────────────────────────────────────────────────────

ws_connections_active = Gauge(
    "roadtrip_websocket_connections_active",
    "Nombre de connexions WebSocket actives",
)

ws_messages = Counter(
    "roadtrip_websocket_messages_total",
    "Nombre de messages WebSocket traités",
    ["direction"],  # direction: inbound | outbound
)

# ── Cache ─────────────────────────────────────────────────────────────────────

cache_operations = Counter(
    "roadtrip_cache_operations_total",
    "Opérations de cache (hits et misses) par type",
    ["cache", "result"],  # cache: osrm|overpass|ocm|elevation|geocoding  result: hit|miss
)


def get_metrics_app():
    """Retourne une ASGI app exposant /metrics au format Prometheus."""
    return make_asgi_app(registry=REGISTRY)
