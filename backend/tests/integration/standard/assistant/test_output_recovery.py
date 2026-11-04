"""Real runtime, protected SDK, ledger and MySQL continuation checkpoints."""

from copy import deepcopy

import pytest
from sqlalchemy import select

from short_drama.agent.runtime.executor import AgentRuntime
from short_drama.domain.assistant.conversations.models import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
)
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import new_task
from tests.support.assistant_runtime import RuntimeTransport, WorkerLost

pytestmark = pytest.mark.integration


def test_recovery_snapshot_matches_events_and_old_cleanup_cannot_clear_new_state(harness_workspace):
    from short_drama.agent.runtime.model_ledger import RunModelLedger
    from short_drama.dao.assistant.execution.run import AgentRunDAO
    from short_drama.service.assistant.execution.run import read_run
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime

    _, accepted = new_task(harness_workspace)
    _, context = runtime(harness_workspace, accepted)
    ledger = RunModelLedger(context)
    with context.factory.begin() as session:
        session.get(AgentRun, context.run_id).status = "running"
    ledger.recovery_state("output_continuation", "first")
    ledger.recovery_state("context_compaction", "second")
    ledger.recovery_state(None, expected_call_id="first")
    with context.factory() as session:
        run = session.get(AgentRun, context.run_id)
        projected = AgentRunDAO(session).projections(run.conversation_id, run_ids=[run.id])[0]
        public = read_run(session, projected, queue_position=0, stopping=False)
        event = session.scalar(
            select(AgentEvent)
            .where(AgentEvent.run_id == run.id, AgentEvent.event_type == "run.recovery")
            .order_by(AgentEvent.seq.desc())
        )
        assert [item.model_dump(mode="json") for item in public.recovery_states] == event.payload[
            "recovery_states"
        ]
        assert public.recovery_states[0].model_call_id == "second"
    ledger.recovery_state(None, expected_call_id="second")
    with context.factory() as session:
        public = read_run(
            session, session.get(AgentRun, context.run_id), queue_position=0, stopping=False
        )
        assert public.recovery_states == []


def test_runtime_delivers_all_fragments_beyond_old_cumulative_limits(harness_workspace):
    factory, settings, _ = harness_workspace
    _, accepted = new_task(harness_workspace)
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        session.get(AgentConversation, run.conversation_id).title = "接续测试"
        run.usage = {**run.usage, "decision_calls": 241, "output_tokens": 2_000_001}
    transport = RuntimeTransport(
        [
            {"text": "第一段", "finish_reason": "length"},
            {"text": "第二段", "finish_reason": "length"},
            {"text": "第三段"},
        ]
    )
    AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        reply = session.scalar(
            select(AgentMessage).where(
                AgentMessage.task_id == accepted.task.id,
                AgentMessage.role == "assistant",
                AgentMessage.kind == "reply",
            )
        )
        assert reply.content == "第一段第二段第三段"
        states = list(
            session.scalars(
                select(AgentEvent.payload).where(
                    AgentEvent.run_id == run.id,
                    AgentEvent.event_type == "run.recovery",
                )
            )
        )
        assert any(item["recovery_states"] for item in states)
        assert states[-1]["recovery_states"] == []
        turns = list(session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)))
        assert all(turn.response.get("raw") for turn in turns)
        assert run.usage["decision_calls"] > 241
        assert run.usage["reserved_output_tokens"] == 0
    assert len(transport.main_requests) == 3


def test_worker_resume_does_not_reemit_durable_fragment(harness_workspace, monkeypatch):
    from datetime import timedelta

    from short_drama.agent.coordinator.recovery import recover
    from short_drama.core.time import utcnow
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService

    factory, settings, _ = harness_workspace
    _, accepted = new_task(harness_workspace)
    with factory.begin() as session:
        session.get(AgentConversation, accepted.run.conversation_id).title = "恢复测试"

    original = AgentToolExecutionService.model_input
    attempts = 0

    def lose_before_second(service, state, identity):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise WorkerLost()
        return original(service, state, identity)

    monkeypatch.setattr(AgentToolExecutionService, "model_input", lose_before_second)
    transport = RuntimeTransport([{"text": "持久片段", "finish_reason": "length"}])
    with pytest.raises(WorkerLost):
        AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        sent = list(
            session.scalars(
                select(AgentTurn).where(
                    AgentTurn.run_id == run.id,
                    AgentTurn.status == "sent",
                )
            )
        )
        assert sent == []
        run.lease_until = utcnow() - timedelta(seconds=1)
        previous_checkpoint = deepcopy(run.checkpoint)
    assert recover(factory, settings) >= 1
    monkeypatch.setattr(AgentToolExecutionService, "model_input", original)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        version = run.message_version
        assert previous_checkpoint.get("recovery_states")
    resumed = RuntimeTransport([{"text": "接续完成"}])
    AgentRuntime(factory, settings, transport=resumed).execute(accepted.run.id, version)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        reply = session.scalar(
            select(AgentMessage).where(
                AgentMessage.task_id == accepted.task.id,
                AgentMessage.kind == "reply",
                AgentMessage.role == "assistant",
            )
        )
        assert reply.content == "持久片段接续完成"
    assert len(resumed.main_requests) == 1
