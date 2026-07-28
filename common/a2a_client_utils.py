"""
Client A2A générique.

Envoie la requête en DataPart (dict JSON natif) et lit la réponse
en DataPart. Fallback TextPart pour compatibilité avec d'anciens agents.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    DataPart,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
)

from a2a.utils import get_data_parts, get_message_text


async def call_agent(base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Résout l'Agent Card de `base_url`, envoie `payload` en DataPart,
    et retourne la réponse en dict (DataPart natif ou TextPart JSON en fallback).
    """
    async with httpx.AsyncClient(timeout=120) as httpx_client:
        resolver = A2ACardResolver(httpx_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()

        client = A2AClient(httpx_client, agent_card=agent_card)

        message = Message(
            role=Role.user,
            message_id=str(uuid.uuid4()),
            parts=[Part(root=DataPart(data=payload))],
        )
        request = SendMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams(message=message),
        )
        response = await client.send_message(request)

    result = response.root.result

    if not isinstance(result, Message):
        raise RuntimeError(
            f"Réponse de type Task non supportée par ce client simplifié : {result}"
        )

    # Priorité DataPart, fallback TextPart JSON
    data_parts = get_data_parts(result.parts)
    if data_parts:
        return data_parts[0]

    text = get_message_text(result)
    return json.loads(text)
