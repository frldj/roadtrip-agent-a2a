"""Client Ollama minimal (streaming) via l'API REST locale."""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

BASE_URL = "http://localhost:11434"

try:
    from monitoring.metrics import (
        llm_generation_rate,
        llm_request_duration,
        llm_requests,
        llm_tokens_generated,
        llm_ttft,
    )
    _METRICS = True
except Exception:
    _METRICS = False


async def ollama_chat(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Single non-streaming call to Ollama, with optional tool definitions."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()


async def list_models() -> list[str]:
    """Retourne les noms des modèles installés, triés par taille croissante."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{BASE_URL}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [m["name"] for m in sorted(models, key=lambda m: m.get("size", 0))]
    except Exception:
        return []


async def chat_stream(
    model: str,
    messages: list[dict[str, Any]],
) -> AsyncIterator[str]:
    """
    Génère les tokens de la réponse du modèle au fur et à mesure.
    Lève une RuntimeError si Ollama n'est pas joignable.
    Enregistre les métriques Prometheus si le module monitoring est disponible.
    """
    payload = {"model": model, "messages": messages, "stream": True}
    t_start = time.perf_counter()
    t_first: float | None = None
    token_count = 0
    status = "success"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{BASE_URL}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        if t_first is None:
                            t_first = time.perf_counter()
                            if _METRICS:
                                llm_ttft.labels(model=model).observe(t_first - t_start)
                        token_count += 1
                        if _METRICS:
                            llm_tokens_generated.labels(model=model).inc()
                        yield token
                    if chunk.get("done"):
                        break

    except httpx.ConnectError:
        status = "error"
        raise RuntimeError(
            "Ollama n'est pas joignable sur http://localhost:11434\n"
            "Lancez-le avec :  ollama serve"
        )
    except Exception:
        status = "error"
        raise
    finally:
        if _METRICS:
            duration = time.perf_counter() - t_start
            llm_request_duration.labels(model=model, status=status).observe(duration)
            llm_requests.labels(model=model, status=status).inc()
            if token_count > 0 and duration > 0:
                llm_generation_rate.labels(model=model).observe(token_count / duration)
