from sqlalchemy import event, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.access.policies.canvas_recycle_access import recycle_scope
from short_drama.db.access.predicates import _scope_options
from short_drama.db.scope.resource_scope import (
    require_project as require_project,
)
from short_drama.db.scope.resource_scope import (
    scope_of as scope_of,
)
from short_drama.domain import (
    AGENT_TABLES,
    CANVAS_TABLES,
    Base,
)


@event.listens_for(Session, "do_orm_execute")
def restrict_queries(state):
    from short_drama.db.access.policies.agent_skill_access import guard_skill_bulk
    from short_drama.db.access.policies.asset_version_access import guard_version_bulk

    guard_skill_bulk(state)
    guard_version_bulk(state)
    actor = state.session.info.get("actor")
    if not actor:
        return
    if not state.is_orm_statement:
        raise WorkflowError(
            "unscoped_query_forbidden", "Use scoped ORM queries for account data", 403
        )
    if (state.is_update or state.is_delete) and any(
        mapper.local_table.name in CANVAS_TABLES for mapper in state.all_mappers
    ):
        raise WorkflowError(
            "guarded_canvas_bulk_write", "Canvas records require checked entity writes", 403
        )
    if (state.is_update or state.is_delete) and any(
        mapper.local_table.name in AGENT_TABLES for mapper in state.all_mappers
    ):
        raise WorkflowError(
            "guarded_agent_bulk_write", "Agent records require checked entity writes", 403
        )
    if (state.is_update or state.is_delete) and any(
        mapper.local_table.name in {"image_groups", "image_group_items", "image_operations"}
        for mapper in state.all_mappers
    ):
        raise WorkflowError("guarded_image_bulk_write", "图片组必须通过版本检查后逐条修改", 403)
    if state.is_update:
        protected = {
            "owner_user_id",
            "scope_user_id",
            "project_id",
            "initiated_by",
            "user_id",
            "media_id",
            "asset_id",
            "reference_media_ids",
            "first_frame_media_id",
            "last_frame_media_id",
            "output_media_id",
            "current_media_id",
            "current_version_id",
            "proxy_media_id",
            "created_by",
            "published_at",
        }
        if any(
            getattr(column, "key", column) in protected for column in state.statement._values or {}
        ):
            raise WorkflowError(
                "guarded_bulk_write", "Ownership and references require checked writes", 403
            )
    if state.is_select or state.is_update or state.is_delete:
        undo_id = state.execution_options.get("assistant_undo_operation_id")
        undo_asset_ids = ()
        if undo_id is not None:
            if (
                not state.is_select
                or type(undo_id) is not int
                or undo_id <= 0
                or {mapper.local_table.name for mapper in state.all_mappers} != {"assets"}
            ):
                raise WorkflowError("invalid_undo_query", "撤销只允许回查原批次的本人素材", 403)
            from short_drama.domain.assistant.tasks.task import AgentOperation

            operation = state.session.scalar(
                select(AgentOperation).where(AgentOperation.id == undo_id)
            )
            if (
                operation is None
                or operation.tool_name != "apply_standard_patch"
                or operation.status != "succeeded"
            ):
                raise NotFound("原撤销批次不存在")
            undo_asset_ids = tuple(
                int(item["id"])
                for item in operation.changes
                if item["kind"] == "asset"
                and item.get("before")
                and item["after"]["deleted"]
                and not item["before"]["deleted"]
            )
        receipt_id = state.execution_options.get("assistant_deletion_receipt_id")
        if receipt_id is not None and (
            not state.is_select
            or type(receipt_id) is not int
            or receipt_id <= 0
            or {mapper.local_table.name for mapper in state.all_mappers} != {"agent_conversations"}
        ):
            raise WorkflowError(
                "invalid_deletion_receipt_query", "删除回执只允许查询本人指定的全局会话", 403
            )
        state.statement = state.statement.options(
            *_scope_options(
                actor.user_id,
                tuple(Base.registry.mappers),
                recycle_scope(state.session),
                receipt_id,
                undo_asset_ids,
            )
        )
