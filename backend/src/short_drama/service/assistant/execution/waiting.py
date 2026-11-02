"""Fence-bound wait registration, durable interrupt binding and safe wakeups."""

import asyncio
from dataclasses import dataclass

from short_drama.agent.context.business_scope import assistant_invocation_locked
from short_drama.agent.core.access import LeaseLost, lock_run, may_decide
from short_drama.agent.core.events import TERMINAL, append_event
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.core.state import mark_scheduled
from short_drama.agent.runtime.events import operation_block_locked
from short_drama.agent.task.queries import task_for_run
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.dao.assistant.execution.waiting import AgentWaitingDAO
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.schemas.assistant.operations.media import GetGenerationStatus
from short_drama.service.assistant.operations.media import get_generation_status_locked


@dataclass(frozen=True)
class WaitSnapshot:
    operation_id: int
    satisfied_without_interrupt: bool
    result: dict


def bound_run(session, run_id, fence):
    rows = lock_run(session, run_id)
    if rows is None or not fence[1] or not owns_execution(rows[2], fence):
        raise LeaseLost()
    project, conversation, run = rows
    task = task_for_run(session, run)
    if (
        run.status in TERMINAL
        or run.cancel_requested
        or task.control_state == "stop_pending"
        or not may_decide(session, project, conversation, run)
    ):
        raise LeaseLost()
    return conversation, task, run


def generation_result_locked(session, conversation, task, run, operation, task_ids):
    identity = assistant_invocation_locked(session, conversation, task, run, operation)
    return get_generation_status_locked(
        session,
        identity,
        GetGenerationStatus(
            project_id=task.execution_target["project_id"], generation_ids=task_ids
        ),
        operation.id,
        None,
    )


def refresh_ready_locked(session, conversation, task, run, operation):
    state = operation.wait_state
    if state["consumed"] or state["ready"] or operation.error:
        return state["ready"]
    if state["kind"] == "generation":
        result = generation_result_locked(
            session, conversation, task, run, operation, state["task_ids"]
        )
        if result["settled"]:
            operation.wait_state = {**state, "ready": True}
            append_event(
                session,
                conversation,
                "wait.ready",
                {
                    "operation_id": str(operation.id),
                    "scope_id": str(operation.scope_id),
                },
                run_id=run.id,
            )
    return operation.wait_state["ready"]


def schedule_ready_locked(session, conversation, task, run):
    if run.status in TERMINAL or run.cancel_requested or task.control_state == "stop_pending":
        return False
    operations = AgentWaitingDAO(session).for_run(run.id)
    ready = False
    for operation in operations:
        state = operation.wait_state
        if state["consumed"] or operation.error:
            continue
        refresh_ready_locked(session, conversation, task, run, operation)
        ready |= bool(operation.wait_state.get("interrupt_id") and operation.wait_state["ready"])
    # 根图仍有可执行分支时不清租约；Worker 完整退出后由 wait 结算安排一次恢复。
    if ready and run.lease_token is None and run.message_status == "idle":
        return mark_scheduled(run, phase="wait")
    return False


def persist_interrupts_locked(
    session, run_id, namespace, checkpoint_id, task_key, interrupts, completed_operations
):
    operations = AgentWaitingDAO(session).for_run(run_id)
    by_id = {str(operation.id): operation for operation in operations}
    for item in interrupts:
        operation = by_id.get(str(item["operation_id"]))
        if operation is None or operation.wait_state["kind"] != item["kind"]:
            raise Conflict("interrupt 未关联到当前 Run 的等待")
        state = operation.wait_state
        if state.get("interrupt_id") not in (None, item["interrupt_id"]):
            raise Conflict("同一工具等待不能替换 interrupt 身份")
        if state.get("interrupt_id"):
            # 子图的 interrupt 会再次冒泡为父图写入；首次持久事实才是实际暂停位置。
            continue
        operation.wait_state = {
            **state,
            "interrupt_id": item["interrupt_id"],
            "namespace": namespace,
            "checkpoint_id": checkpoint_id,
            "graph_task_id": task_key,
        }
    for operation in operations:
        if str(operation.id) in completed_operations and operation.finished_at is not None:
            operation.wait_state = {**operation.wait_state, "consumed": True}


class GenerationWaitService:
    def __init__(self, context):
        self.context = context

    def inspect_and_register(self, run_id, scope_id, tool_call_id, task_ids, *, fence):
        with self.context.factory.begin() as session:
            conversation, task, run = bound_run(session, run_id, fence)
            operation = AgentWaitingDAO(session).operation(run.id, scope_id, tool_call_id)
            if operation is None or operation.tool_name != "runtime__wait_generation":
                raise NotFound("等待工具调用不存在")
            result = generation_result_locked(session, conversation, task, run, operation, task_ids)
            if operation.wait_state is None:
                operation.wait_state = {
                    "kind": "generation",
                    "task_ids": [str(i) for i in task_ids],
                    "scope_id": str(scope_id),
                    "tool_call_id": str(operation.tool_call_id),
                    "requires_interrupt": not result["settled"],
                    "ready": result["settled"],
                    "consumed": False,
                }
                if not result["settled"]:
                    operation.status, operation.phase = "waiting_media", "waiting_media"
                    operation_block_locked(session, conversation, run, operation)
                    session.get(AgentToolCall, operation.tool_call_id).status = "waiting_generation"
                    append_event(
                        session,
                        conversation,
                        "wait.requested",
                        {
                            "operation_id": str(operation.id),
                            "scope_id": str(scope_id),
                            "kind": "generation",
                            "task_ids": operation.wait_state["task_ids"],
                        },
                        run_id=run.id,
                    )
            elif operation.wait_state["task_ids"] != [str(i) for i in task_ids]:
                raise Conflict("等待依赖不能在重放时改变")
            return WaitSnapshot(
                operation.id, not operation.wait_state["requires_interrupt"], result
            )

    def read_result(self, operation_id, *, fence):
        with self.context.factory.begin() as session:
            conversation, task, run = bound_run(session, self.context.run_id, fence)
            operation = session.get(AgentOperation, operation_id)
            if (
                operation is None
                or operation.run_id != run.id
                or operation.scope_id != self.context.scope_id
            ):
                raise NotFound("等待不存在")
            result = generation_result_locked(
                session, conversation, task, run, operation, operation.wait_state["task_ids"]
            )
            if not result["settled"]:
                raise Conflict("生成任务尚未完成")
            return result

    def resume_values(self, claim, snapshot):
        with self.context.factory.begin() as session:
            conversation, task, run = bound_run(session, claim.run_id, claim.fence)
            result = {}
            for operation in AgentWaitingDAO(session).for_run(run.id):
                state = operation.wait_state
                if not state["consumed"] and not operation.error:
                    refresh_ready_locked(session, conversation, task, run, operation)
                    if operation.wait_state["ready"] and state.get("interrupt_id"):
                        result[state["interrupt_id"]] = {"ready": True}
            return result

    async def bind_snapshot(self, snapshot, claim):
        # saver 已在 pending writes 同一事务中保存真实 namespace / interrupt id。
        await asyncio.to_thread(self._bind_snapshot, snapshot, claim)

    def _bind_snapshot(self, snapshot, claim):
        visible = {}

        def collect(current):
            for item in current.interrupts:
                if isinstance(item.value, dict) and item.value.get("wait_operation_id"):
                    visible[item.value["wait_operation_id"]] = item.id
            for item in current.tasks:
                if hasattr(item.state, "interrupts"):
                    collect(item.state)

        collect(snapshot)
        with self.context.factory.begin() as session:
            conversation, task, run = bound_run(session, claim.run_id, claim.fence)
            for operation in AgentWaitingDAO(session).for_run(run.id):
                state = operation.wait_state
                if (
                    str(operation.id) in visible
                    and state.get("interrupt_id") != visible[str(operation.id)]
                ):
                    raise Conflict("可见 interrupt 与持久写入关联不一致")
                refresh_ready_locked(session, conversation, task, run, operation)
            self._bind_parent_waits(session, conversation, task, run, visible)

    @staticmethod
    def _bind_parent_waits(session, conversation, task, run, visible):
        from sqlalchemy import select

        from short_drama.agent.task.state import task_event

        by_scope = {}
        for operation in AgentWaitingDAO(session).for_run(run.id):
            if str(operation.id) in visible and not operation.wait_state["consumed"]:
                by_scope.setdefault(operation.scope_id, []).append(operation)
        for scope in session.scalars(
            select(AgentExecutionScope).where(
                AgentExecutionScope.run_id == run.id, AgentExecutionScope.id.in_(by_scope)
            )
        ):
            if scope.parent_tool_call_id is None:
                continue
            parent = session.scalar(
                select(AgentOperation).where(
                    AgentOperation.tool_call_id == scope.parent_tool_call_id
                )
            )
            if parent is None or parent.finished_at is not None:
                continue
            user = any(row.wait_state["kind"] == "user_input" for row in by_scope[scope.id])
            parent.status = parent.phase = "waiting_input" if user else "waiting_media"
            parent.row_version += 1
            task_event(
                session,
                conversation,
                task,
                "step.updated",
                run=run,
                operation_id=str(parent.id),
                scope_id=str(scope.parent_scope_id),
                child_scope_id=str(scope.id),
                phase=parent.phase,
                trace_version=str(parent.row_version),
            )


def collect_ready_waits(factory, settings, limit=100):
    with factory() as session:
        ids = AgentWaitingDAO(session).candidates(limit)
    count = 0
    for run_id in ids:
        with factory.begin() as session:
            rows = lock_run(session, run_id, skip_locked=True)
            if rows is None:
                continue
            project, conversation, run = rows
            if not settings.agent_enabled or not may_decide(session, project, conversation, run):
                continue
            count += int(
                schedule_ready_locked(session, conversation, task_for_run(session, run), run)
            )
    return count
