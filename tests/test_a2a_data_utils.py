"""Tests des helpers de création de messages A2A."""
from a2a.types import Role, TaskState

from common.a2a_data_utils import new_agent_data_message, new_status_event


class TestNewAgentDataMessage:
    def test_role_is_agent(self):
        msg = new_agent_data_message({"key": "value"})
        assert msg.role == Role.agent

    def test_context_and_task_ids(self):
        msg = new_agent_data_message({}, context_id="ctx1", task_id="task1")
        assert msg.context_id == "ctx1"
        assert msg.task_id == "task1"

    def test_has_one_part(self):
        msg = new_agent_data_message({"a": 1})
        assert len(msg.parts) == 1

    def test_unique_message_ids(self):
        msg1 = new_agent_data_message({})
        msg2 = new_agent_data_message({})
        assert msg1.message_id != msg2.message_id

    def test_none_ids_are_allowed(self):
        msg = new_agent_data_message({}, context_id=None, task_id=None)
        assert msg.context_id is None
        assert msg.task_id is None

    def test_arbitrary_data_payload(self):
        payload = {"route": [1, 2, 3], "nested": {"x": True}}
        msg = new_agent_data_message(payload)
        assert msg is not None


class TestNewStatusEvent:
    def test_state_is_working(self):
        evt = new_status_event("Calcul en cours…", "ctx1", "task1")
        assert evt.status.state == TaskState.working

    def test_not_final(self):
        evt = new_status_event("test", "ctx1", "task1")
        assert evt.final is False

    def test_context_and_task_ids(self):
        evt = new_status_event("msg", "ctx42", "task99")
        assert evt.context_id == "ctx42"
        assert evt.task_id == "task99"

    def test_none_ids_become_empty_string(self):
        evt = new_status_event("msg", None, None)
        assert evt.context_id == ""
        assert evt.task_id == ""

    def test_status_message_is_set(self):
        evt = new_status_event("Géocodage…", "ctx", "task")
        assert evt.status.message is not None
        assert evt.status.message.role == Role.agent
