"""Only completed native task receipts transfer immutable child business results."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.assistant.tasks.task import AgentOperation

BUSINESS_OUTPUT_TOOLS = frozenset(
    {
        "workbench__apply_standard_patch",
        "workbench__apply_canvas_patch",
        "workbench__set_image_group_primary",
        "workbench__submit_generation_batch",
    }
)


def _business_outputs():
    return (
        select(AgentOperation)
        .join(
            BusinessToolInvocation,
            BusinessToolInvocation.id == AgentOperation.business_invocation_id,
        )
        .where(
            AgentOperation.status == "succeeded",
            AgentOperation.tool_name.in_(BUSINESS_OUTPUT_TOOLS),
            BusinessToolInvocation.status == "succeeded",
            BusinessToolInvocation.source == "assistant",
            BusinessToolInvocation.tool_name == AgentOperation.tool_name,
        )
        .order_by(AgentOperation.id)
    )


def business_outputs_locked(session: Session, run_id: int, scope_id: int) -> list[AgentOperation]:
    return list(
        session.scalars(
            _business_outputs().where(
                AgentOperation.run_id == run_id, AgentOperation.scope_id == scope_id
            )
        ).all()
    )


def inherited_business_outputs_locked(
    session: Session, run: AgentRun, scope_id: int
) -> list[AgentOperation]:
    scope = session.get(AgentExecutionScope, scope_id)
    if scope.parent_scope_id is not None:
        return []
    identifiers = run.checkpoint.get("inherited_business_operation_ids", [])
    if not identifiers:
        return []
    return list(
        session.scalars(
            _business_outputs().where(
                AgentOperation.task_id == run.task_id,
                AgentOperation.id.in_([int(value) for value in identifiers]),
                BusinessToolInvocation.owner_user_id == run.initiated_by,
            )
        )
    )


def visible_business_outputs_locked(
    session: Session, run: AgentRun, scope_id: int
) -> list[AgentOperation]:
    return sorted(
        [
            *business_outputs_locked(session, run.id, scope_id),
            *delivered_business_outputs_locked(session, run.id, scope_id),
            *inherited_business_outputs_locked(session, run, scope_id),
        ],
        key=lambda item: item.id,
    )


def delivered_business_outputs_locked(
    session: Session, run_id: int, scope_id: int
) -> list[AgentOperation]:
    deliveries = session.execute(
        select(AgentOperation.tool_call_id, AgentOperation.result).where(
            AgentOperation.run_id == run_id,
            AgentOperation.scope_id == scope_id,
            AgentOperation.tool_name == "task",
            AgentOperation.status == "succeeded",
        )
    ).all()
    sources = {
        call_id: {int(item["operation_id"]) for item in result.get("business_receipts", [])}
        for call_id, result in deliveries
    }
    identifiers = {identifier for values in sources.values() for identifier in values}
    if not identifiers:
        return []
    # 同时核对清单与真实父调用；摘要中的其他子scope/Operation身份不授予权限。
    rows = session.execute(
        _business_outputs()
        .add_columns(AgentExecutionScope.parent_tool_call_id)
        .join(AgentExecutionScope, AgentExecutionScope.id == AgentOperation.scope_id)
        .where(
            AgentOperation.run_id == run_id,
            AgentOperation.id.in_(identifiers),
            AgentExecutionScope.run_id == run_id,
            AgentExecutionScope.parent_scope_id == scope_id,
            AgentExecutionScope.parent_tool_call_id.in_(sources),
        )
    ).all()
    return [operation for operation, parent_call in rows if operation.id in sources[parent_call]]
