"""Stable execution scopes share one Run lease and parent authorization."""

from collections.abc import Sequence
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.agent.context.business_scope import assistant_invocation_locked
from short_drama.agent.context.delegation import parse_inputs
from short_drama.agent.resources.catalog import authorized_reference_locked
from short_drama.agent.task.queries import task_for_run
from short_drama.agent.task.sources import source_projection
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.dao.assistant.execution.run import AgentRunDAO
from short_drama.dao.assistant.execution.scope import AgentExecutionScopeDAO
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.utils.snowflake import next_id


def root_scope_locked(session, run):
    dao = AgentExecutionScopeDAO(session, owner_id=run.initiated_by, run_id=run.id)
    scope = dao.get(scope_key="root")
    if scope is None:
        scope = AgentExecutionScope(
            id=next_id(), owner_user_id=run.initiated_by, run_id=run.id, scope_key="root"
        )
        session.add(scope)
        session.flush()
    return scope


def sandbox_scope_locked(session: Session, run: AgentRun, scope_id: int) -> AgentExecutionScope:
    """Caller must hold the Run lock and validate its live fence or maintenance authority."""
    scope = AgentExecutionScopeDAO(session, owner_id=run.initiated_by, run_id=run.id).get(
        scope_id=scope_id
    )
    if scope is None:
        raise NotFound("执行范围不存在")
    return scope


def sandbox_scopes_locked(session: Session, run: AgentRun) -> Sequence[AgentExecutionScope]:
    return AgentExecutionScopeDAO(session, owner_id=run.initiated_by, run_id=run.id).workspaces()


class AgentExecutionScopeService:
    def __init__(self, graph_service):
        self.graph = graph_service

    def _dao(self, session):
        return AgentExecutionScopeDAO(
            session, owner_id=self.graph.owner_id, run_id=self.graph.run_id
        )

    def root(self):
        with self.graph.factory.begin() as session:
            self.graph._bound(session)
            scope = self._dao(session).get(scope_key="root")
            if scope is None:
                raise NotFound("主执行范围不存在")
            return scope

    def get(self, scope_id):
        with self.graph.factory.begin() as session:
            self.graph._bound(session)
            scope = self._dao(session).get(scope_id=scope_id)
            if scope is None:
                raise NotFound("执行范围不存在")
            return scope

    def child(self, parent_scope_id, parent_tool_call_id):
        with self.graph.factory.begin() as session:
            self.graph._bound(session)
            dao = self._dao(session)
            parent = dao.get(scope_id=parent_scope_id)
            call = session.get(AgentToolCall, parent_tool_call_id)
            if (
                parent is None
                or call is None
                or call.run_id != self.graph.run_id
                or call.scope_id != parent_scope_id
                or call.tool_name != "task"
            ):
                raise NotFound("委派调用不属于此执行范围")
            if parent.parent_scope_id is not None:
                raise Conflict("子任务不允许再次委派")
            key = f"tool:{parent_tool_call_id}"
            scope = dao.get(scope_key=key)
            if scope is None:
                scope = AgentExecutionScope(
                    id=next_id(),
                    owner_user_id=self.graph.owner_id,
                    run_id=self.graph.run_id,
                    scope_key=key,
                    parent_scope_id=parent_scope_id,
                    parent_tool_call_id=parent_tool_call_id,
                )
                session.add(scope)
                session.flush()
            return scope

    def for_delegation(
        self, run_id, parent_scope_id, provider_call_id, *, fence, memory_epoch
    ) -> int:
        if run_id != self.graph.run_id or fence != self.graph.fence:
            raise NotFound("委派调用不属于此执行租约")
        with self.graph.factory.begin() as session:
            self.graph._bound(session)
            parent = self._dao(session).get(scope_id=parent_scope_id)
            call = AgentRunDAO(session).tool_call(run_id, parent_scope_id, provider_call_id)
            if parent is None or call is None or call.tool_name != "task":
                raise NotFound("委派调用尚未由父模型边界登记")
            if parent.parent_scope_id is not None:
                raise Conflict("子任务不允许再次委派")
            scope = self._dao(session).get(scope_key=f"tool:{call.id}")
            if scope is not None:
                return scope.id
            operation = session.scalar(
                select(AgentOperation).where(AgentOperation.tool_call_id == call.id)
            )
            if operation is None:
                raise NotFound("委派调用尚未由工具边界登记")
            inputs = self._freeze_inputs(session, operation, call)
            inputs["memory_epoch"] = memory_epoch
            operation.result = {**(operation.result or {}), "delegation_inputs": inputs}
            scope = AgentExecutionScope(
                id=next_id(),
                owner_user_id=self.graph.owner_id,
                run_id=run_id,
                scope_key=f"tool:{call.id}",
                parent_scope_id=parent_scope_id,
                parent_tool_call_id=call.id,
            )
            session.add(scope)
            session.flush()
            return scope.id

    def _freeze_inputs(self, session, operation, call):
        from deepagents.middleware.subagents import TaskToolSchema
        from pydantic import ValidationError

        from short_drama.agent.core.access import LeaseLost, lock_run, may_decide
        from short_drama.core.exceptions import WorkflowError

        project, conversation, run = lock_run(session, self.graph.run_id)
        if run.cancel_requested or not may_decide(session, project, conversation, run):
            raise LeaseLost()
        task = task_for_run(session, run)
        try:
            request = TaskToolSchema.model_validate(call.arguments["parsed"])
        except ValidationError as error:
            raise WorkflowError("invalid_tool_arguments", "委派工具参数无效", 422) from error
        if request.subagent_type != "workbench-task":
            raise WorkflowError("assistant_delegation_type", "只能委派给通用工作台子任务", 422)
        declared = parse_inputs(request.description)
        resources = [
            authorized_reference_locked(session, conversation, task, reference).model_dump(
                mode="json"
            )
            for reference in declared.resources
        ]
        snapshots, generations, script = {}, set(), None
        if task.execution_target:
            authority = assistant_invocation_locked(
                session, conversation, task, run, operation
            ).scope
            available = {**authority.subject_snapshots}
            available.update(
                (item["subject_key"], item)
                for item in authority.subject_snapshots.values()
                if item.get("subject_key")
            )
            if (
                set(declared.subjects) - available.keys()
                or {int(identifier) for identifier in declared.generation_ids}
                - authority.generation_ids
            ):
                raise WorkflowError("assistant_delegation_scope", "交接引用超出父任务授权范围", 403)
            snapshots = self._freeze_subjects(session, task, available, declared.subjects)
            generations = {int(identifier) for identifier in declared.generation_ids}
            script = deepcopy(authority.script_reference) if declared.script else None
        elif declared.subjects or declared.generation_ids or declared.script:
            raise WorkflowError("assistant_delegation_scope", "父任务没有所声明的业务范围", 403)
        return {
            "task_sources": source_projection(
                [{"id": str(call.id), "seq": 0, "content": request.description}], set()
            ),
            "context_snapshot": {"schema_version": 2, "subjects": list(snapshots.values())},
            "resources": resources,
            "generation_ids": sorted(str(value) for value in generations),
            "script_reference": script,
        }

    def _freeze_subjects(self, session, task, available, keys):
        from short_drama.dao.assistant.context.context import AssistantContextDAO
        from short_drama.schemas.assistant.tasks.task import TaskSubjectReference
        from short_drama.service.assistant.context.subject import freeze_subject

        target, result = task.execution_target, {}
        for key in keys:
            source = available[key]
            kind = source.get("kind") or key.split(":", 1)[0]
            kind = "node" if kind == "canvas_node" else kind
            reference = TaskSubjectReference(
                project_id=target["project_id"],
                source_kind="canvas" if kind == "node" else "project",
                source_id=target.get("canvas_id") or target["project_id"],
                kind=kind,
                id=source["id"],
                revision=source["revision"],
            )
            frozen = freeze_subject(AssistantContextDAO(session), self.graph.owner_id, reference)
            if source.get("carried_source_message_id"):
                frozen["carried_source_message_id"] = source["carried_source_message_id"]
            result[frozen["subject_key"]] = frozen
        return result
