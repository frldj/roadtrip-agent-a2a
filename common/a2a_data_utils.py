"""Utilitaires pour créer des messages A2A avec DataPart (données structurées)."""
from __future__ import annotations

import uuid
from typing import Any

from a2a.types import (
    DataPart,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


def new_agent_data_message(
    data: dict[str, Any],
    context_id: str | None = None,
    task_id: str | None = None,
) -> Message:
    """Crée un message agent dont le contenu est un DataPart (dict JSON natif)."""
    return Message(
        role=Role.agent,
        message_id=str(uuid.uuid4()),
        parts=[Part(root=DataPart(data=data))],
        context_id=context_id,
        task_id=task_id,
    )


def new_status_event(
    text: str,
    context_id: str | None,
    task_id: str | None,
) -> TaskStatusUpdateEvent:
    """Crée un TaskStatusUpdateEvent 'working' (non final) avec un message texte."""
    return TaskStatusUpdateEvent(
        context_id=context_id or "",
        task_id=task_id or "",
        status=TaskStatus(
            state=TaskState.working,
            message=Message(
                role=Role.agent,
                message_id=str(uuid.uuid4()),
                parts=[Part(root=TextPart(text=text))],
            ),
        ),
        final=False,
    )
