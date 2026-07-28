from __future__ import annotations

import json
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import get_data_parts

from common.a2a_data_utils import new_agent_data_message, new_status_event
from common.schemas import RoutePlanRequest
from route_agent.core import plan_route

logger = logging.getLogger(__name__)


class RouteAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            data_parts = get_data_parts(context.message.parts)
            if data_parts:
                payload = data_parts[0]
            else:
                payload = json.loads(context.get_user_input())

            req = RoutePlanRequest.model_validate(payload)

            await event_queue.enqueue_event(new_status_event(
                f"Géocodage et calcul d'itinéraire : {req.origin} → {req.destination}…",
                context.context_id, context.task_id,
            ))

            result = await plan_route(req)
            response = new_agent_data_message(
                result.model_dump(), context_id=context.context_id
            )
        except Exception as exc:
            logger.exception("Erreur route_agent")
            response = new_agent_data_message(
                {"error": str(exc)}, context_id=context.context_id
            )

        await event_queue.enqueue_event(response)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported for route_agent")
