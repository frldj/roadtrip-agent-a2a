from __future__ import annotations

import json
import logging
import os

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import get_data_parts

from accommodation_agent.core import plan_accommodation
from common.a2a_data_utils import new_agent_data_message, new_status_event
from common.schemas import AccommodationRequest

logger = logging.getLogger(__name__)


class AccommodationAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            data_parts = get_data_parts(context.message.parts)
            if data_parts:
                payload = data_parts[0]
            else:
                payload = json.loads(context.get_user_input())

            req = AccommodationRequest.model_validate(payload)

            n_segs = len(req.segments)
            source = "Google Places" if os.getenv("GOOGLE_PLACES_KEY") else "OpenStreetMap"
            await event_queue.enqueue_event(new_status_event(
                f"Recherche d'hébergements via {source} ({n_segs} étape(s))…",
                context.context_id, context.task_id,
            ))

            result = await plan_accommodation(req)
            response = new_agent_data_message(
                result.model_dump(), context_id=context.context_id
            )
        except Exception as exc:
            logger.exception("Erreur accommodation_agent")
            response = new_agent_data_message(
                {"error": str(exc)}, context_id=context.context_id
            )

        await event_queue.enqueue_event(response)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported for accommodation_agent")
