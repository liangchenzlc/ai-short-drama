"""Execution scope and concurrent operation constraints on real MySQL."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.execution.models import AgentToolCall, AgentTurn
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.utils.snowflake import next_id
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
)
from tests.integration.standard.assistant.test_graph_checkpointer import bound

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


def calls_for(factory, accepted, scope_id, *, tool_name="task"):
    with factory.begin() as session:
        turn = AgentTurn(
            id=next_id(),
            run_id=accepted.run.id,
            scope_id=scope_id,
            turn_no=1,
            request_messages=[{"kwargs": {"tool_version": 2}}],
            usage={},
        )
        session.add(turn)
        session.flush()
        calls = []
        for index in range(1, 4):
            call = AgentToolCall(
                id=next_id(),
                run_id=accepted.run.id,
                scope_id=scope_id,
                turn_id=turn.id,
                call_index=index,
                provider_call_id=f"task-{index}",
                tool_name=tool_name,
                arguments={"parsed": {}},
                arguments_hash="a" * 64,
                idempotency_key=f"{next_id():064d}",
            )
            session.add(call)
            calls.append(call.id)
        return calls


def test_child_scopes_are_idempotent_and_three_operations_can_coexist(task_workspace):
    from short_drama.service.assistant.execution.scope import AgentExecutionScopeService

    _, accepted = new_task(task_workspace)
    graph = bound(task_workspace, accepted)
    scopes = AgentExecutionScopeService(graph)
    root = scopes.root()
    calls = calls_for(graph.factory, accepted, root.id)
    with ThreadPoolExecutor(max_workers=3) as pool:
        children = list(pool.map(lambda call: scopes.child(root.id, call), calls))
    assert len({child.id for child in children}) == 3
    assert scopes.child(root.id, calls[0]).id == children[0].id
    assert all(
        child.run_id == accepted.run.id and child.parent_scope_id == root.id for child in children
    )
    with graph.factory.begin() as session:
        for call_id in calls:
            session.add(
                AgentOperation(
                    id=next_id(),
                    task_id=accepted.task.id,
                    run_id=accepted.run.id,
                    scope_id=root.id,
                    tool_call_id=call_id,
                    tool_name="task",
                    plan_revision=1,
                    input_revision=1,
                    payload_hash="a" * 64,
                    arguments={},
                    result_refs=[],
                    changes=[],
                )
            )
        session.flush()
        assert len(AgentTaskDAO(session).active_operations(accepted.task.id)) == 3
        assert AgentTaskDAO(session).has_active_operations(accepted.task.id)
    with graph.factory.begin() as session:
        rows = session.scalars(
            select(AgentOperation).where(AgentOperation.task_id == accepted.task.id)
        ).all()
        assert len(rows) == 3


def test_scope_guards_reject_foreign_owner_and_cross_run_sql(task_workspace):
    from sqlalchemy.exc import IntegrityError

    from short_drama.core.exceptions import NotFound
    from short_drama.domain.assistant.execution.scope import AgentExecutionScope
    from short_drama.service.assistant.execution.scope import AgentExecutionScopeService
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    _, accepted = new_task(task_workspace)
    graph = bound(task_workspace, accepted)
    root = AgentExecutionScopeService(graph).root()
    foreign = AgentExecutionScopeService(bound(task_workspace, accepted, owner=2))
    with pytest.raises(NotFound):
        foreign.get(root.id)
    with graph.factory.begin() as session:
        session.info["actor"] = task_actor(2)
        assert session.get(AgentExecutionScope, root.id) is None
    with graph.factory.begin() as session:
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                AgentTurn(
                    id=next_id(),
                    run_id=accepted.run.id,
                    scope_id=next_id(),
                    turn_no=1,
                    request_messages=[],
                    usage={},
                )
            )
            session.flush()


def test_claim_specific_calls_does_not_block_siblings(task_workspace):
    from short_drama.domain.assistant.execution.models import AgentRun
    from short_drama.domain.assistant.tasks.task import AgentTask
    from short_drama.service.assistant.execution.tools import AgentToolExecutionService
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime

    _, accepted = new_task(task_workspace)
    _saver, context = runtime(task_workspace, accepted)
    identifiers = calls_for(
        context.factory, accepted, context.scope_id, tool_name="runtime__get_generation_policy"
    )
    with context.factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.status, run.phase = "running", "tools"
        task = session.get(AgentTask, accepted.task.id)
        task.applied_input_revision = task.input_revision
        calls = session.scalars(
            select(AgentToolCall).where(AgentToolCall.id.in_(identifiers))
        ).all()
    state = {"permitted_tool_names": ["runtime__get_generation_policy"]}

    def start(call):
        return AgentToolExecutionService(context).start_tool(
            {"id": call.provider_call_id, "name": call.tool_name, "args": {}}, state
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        claimed = [result[0] for result in pool.map(start, calls)]
    assert len(set(claimed)) == 3 and None not in claimed
    assert start(calls[0])[0] == claimed[0]
    with context.factory() as session:
        assert len(AgentTaskDAO(session).active_operations(accepted.task.id)) == 3
