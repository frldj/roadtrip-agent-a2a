"""Exécuteur A2A pour l'agent Validateur."""
from __future__ import annotations

import json
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import get_data_parts

from common.a2a_data_utils import new_agent_data_message
from common.schemas import PlanValidationRequest
from validator_agent.core import validate_plan

logger = logging.getLogger(__name__)


class ValidatorAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            data_parts = get_data_parts(context.message.parts)
            payload = data_parts[0] if data_parts else json.loads(context.get_user_input())
            req = PlanValidationRequest.model_validate(payload)
            result = validate_plan(req)
            response = new_agent_data_message(
                result.model_dump(), context_id=context.context_id
            )
        except Exception as exc:
            logger.exception("Erreur validator_agent")
            response = new_agent_data_message(
                {"error": str(exc)}, context_id=context.context_id
            )
        await event_queue.enqueue_event(response)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError
