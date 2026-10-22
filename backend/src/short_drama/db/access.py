"""Scoped ORM queries plus write/reference checks. API sessions always carry an actor.

Subqueries use Core aliases intentionally: applying ORM criteria inside their own
authorization predicate would recurse. Unscoped sessions are reserved for workers,
authentication and migration tools, never accepted from an HTTP payload.
"""

from functools import lru_cache

from sqlalchemy import and_, event, false, inspect, or_, select
from sqlalchemy.orm import Mapper, Session, with_loader_criteria
from sqlalchemy.orm.interfaces import ORMOption

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.time import utcnow
from short_drama.domain import (
    AGENT_PRIVATE_TABLES,
    AGENT_TABLES,
    CANVAS_PRIVATE_TABLES,
    CANVAS_TABLES,
    AIModelConfig,
    Base,
)
from short_drama.domain.collaboration import AuditEvent, ResourceScope
from short_drama.utils.snowflake import next_id

from .canvas_recycle_access import CanvasRecycleScope, recycle_scope
from .canvas_resource_scope import copy_ancestors
from .resource_scope import (
    _agent_conversation,
    _agent_parent,
    _canvas_library_parent,
    _canvas_upload_parent,
)
from .resource_scope import (
    require_project as require_project,
)
from .resource_scope import (
    scope_of as scope_of,
)


def project_ids(user_id, *, include_archived=False):
    projects = Base.metadata.tables["projects"].alias("allowed_projects")
    return select(projects.c.id).where(
        True if include_archived else projects.c.archived_at.is_(None),
        projects.c.owner_user_id == user_id,
    )


def allowed_ids(name, user_id):
    table = Base.metadata.tables[name].alias(f"allowed_{name}")
    return select(table.c.id).where(condition(table, user_id))


def condition(table, user_id):
    name = table.original.name if hasattr(table, "original") else table.name
    c = table.c
    if name == "image_group_items":
        return c.group_id.in_(allowed_ids("image_groups", user_id))
    if name == "image_operations":
        return and_(
            c.created_by == user_id,
            c.item_id.in_(allowed_ids("image_group_items", user_id)),
        )
    if name == "canvas_style_media_references":
        return c.style_id.in_(allowed_ids("canvas_style_profiles", user_id))
    if name == "canvas_local_jobs":
        return and_(
            c.user_id == user_id,
            or_(c.project_id.is_(None), c.project_id.in_(project_ids(user_id))),
        )
    if name in {
        "canvas_workspace_user_states",
        "canvas_style_preferences",
        "canvas_model_catalogs",
        "canvas_channel_models",
        "canvas_library_folders",
        "canvas_project_folders",
        "canvas_project_folder_items",
        "canvas_resource_deletions",
        "canvas_creation_attempts",
        "canvas_creation_resources",
    }:
        return c.user_id == user_id
    if name == "canvas_library_assets":
        return and_(
            c.user_id == user_id,
            or_(c.project_id.is_(None), c.project_id.in_(project_ids(user_id))),
        )
    if name in {"canvas_library_asset_references", "canvas_library_folder_items"}:
        return c.library_asset_id.in_(allowed_ids("canvas_library_assets", user_id))
    if name in {"canvas_resource_chunks", "canvas_resource_copy_sources"}:
        return c.upload_id.in_(allowed_ids("canvas_resource_uploads", user_id))
    if name == "canvas_resource_uploads":
        return and_(
            c.user_id == user_id,
            or_(c.scope_user_id == user_id, c.project_id.in_(project_ids(user_id))),
        )
    if name == "canvas_task_bindings":
        return and_(c.initiated_by == user_id, c.project_id.in_(project_ids(user_id)))
    if name in {"canvas_task_media_references", "canvas_task_text_deltas", "canvas_results"}:
        return and_(
            c.task_binding_id.in_(allowed_ids("canvas_task_bindings", user_id)),
            c.created_by == user_id,
            c.project_id.in_(project_ids(user_id)),
        )
    if name in CANVAS_PRIVATE_TABLES:
        owner = c.actor_user_id if name == "canvas_write_receipts" else c.user_id
        return and_(
            owner == user_id,
            c.project_id.in_(
                project_ids(user_id, include_archived=name == "canvas_write_receipts")
            ),
        )
    if name == "user_creation_drafts":
        return and_(c.user_id == user_id, c.project_id.in_(project_ids(user_id)))
    # Private execution data never inherits the ordinary shared-project predicate.
    if name == "agent_conversations":
        return and_(
            c.owner_user_id == user_id,
            c.status != "deleted",
            or_(
                and_(c.scope_version == 3, c.project_id.is_(None)),
                c.project_id.in_(project_ids(user_id)),
            ),
        )
    if name == "agent_tasks":
        return c.conversation_id.in_(allowed_ids("agent_conversations", user_id))
    if name == "agent_memory_items":
        return and_(
            c.owner_user_id == user_id,
            or_(
                c.scope_type == "user",
                and_(
                    c.scope_type == "conversation",
                    c.conversation_id.in_(allowed_ids("agent_conversations", user_id)),
                ),
                and_(c.scope_type == "project", c.project_id.in_(project_ids(user_id))),
            ),
        )
    if name in {"agent_memory_revisions", "agent_memory_sources", "agent_memory_forget_rules"}:
        return c.memory_id.in_(allowed_ids("agent_memory_items", user_id))
    if name in {"agent_conversation_summaries", "agent_memory_jobs"}:
        return and_(
            c.owner_user_id == user_id,
            c.conversation_id.in_(allowed_ids("agent_conversations", user_id)),
        )
    if name in {
        "agent_task_inputs",
        "agent_task_controls",
        "agent_operations",
        "agent_media_requests",
    }:
        return c.task_id.in_(allowed_ids("agent_tasks", user_id))
    if name in {
        "agent_messages",
        "agent_runs",
        "agent_events",
        "agent_discussion_revisions",
        "agent_batch_undos",
    }:
        return c.conversation_id.in_(allowed_ids("agent_conversations", user_id))
    if name in {"agent_turns", "agent_tool_calls"}:
        return c.run_id.in_(allowed_ids("agent_runs", user_id))
    if name == "agent_artifacts":
        return and_(c.created_by == user_id, c.project_id.in_(project_ids(user_id)))
    if name == "agent_skill_versions":
        return or_(c.owner_user_id == user_id, c.owner_user_id.is_(None))
    if name in {"agent_skills", "agent_preferences", "agent_skill_settings"}:
        return c.owner_user_id == user_id
    if name == "agent_attachments":
        return and_(
            c.owner_user_id == user_id,
            c.conversation_id.in_(allowed_ids("agent_conversations", user_id)),
        )
    if name in {"async_tasks", "generation_batches"}:
        return and_(
            c.initiated_by == user_id,
            or_(c.scope_user_id == user_id, c.project_id.in_(project_ids(user_id))),
        )
    if name == "project_render_jobs":
        return and_(
            c.initiated_by == user_id,
            c.assembly_id.in_(allowed_ids("project_assemblies", user_id)),
        )
    if name == "project_assembly_clips":
        return c.assembly_id.in_(allowed_ids("project_assemblies", user_id))
    if name == "project_scripts":
        return and_(
            or_(c.created_by == user_id, c.published_at.is_not(None)),
            c.project_id.in_(allowed_ids("projects", user_id)),
        )
    if name in {"novel_script_records", "script_shot_records"}:
        return and_(
            c.created_by == user_id, c.script_id.in_(allowed_ids("project_scripts", user_id))
        )
    if name == "asset_image_candidates":
        return and_(c.created_by == user_id, c.asset_id.in_(allowed_ids("assets", user_id)))
    if name in {"media_files", "canvas_binary_resources"}:
        return or_(
            c.scope_user_id == user_id,
            and_(
                or_(c.created_by == user_id, c.published_at.is_not(None)),
                c.project_id.in_(project_ids(user_id)),
            ),
        )
    if name == "projects":
        return and_(c.archived_at.is_(None), c.owner_user_id == user_id)
    if name == "assets":
        return and_(c.project_id.in_(project_ids(user_id)), c.deleted_at.is_(None))
    if name == "ai_model_configs":
        return c.owner_user_id == user_id
    if "scope_user_id" in c:
        return (
            or_(c.scope_user_id == user_id, c.project_id.in_(project_ids(user_id)))
            if "project_id" in c
            else c.scope_user_id == user_id
        )
    if name == "users":
        return c.id == user_id
    if name in {
        "user_sessions",
        "email_challenges",
        "user_model_preferences",
        "user_project_states",
    }:
        return c.user_id == user_id
    if name == "audit_events":
        return or_(
            and_(
                c.project_id.in_(project_ids(user_id)),
                or_(
                    c.actor_user_id == user_id,
                    c.object_type.not_in(
                        [
                            *CANVAS_PRIVATE_TABLES,
                            "async_tasks",
                            "ai_generation_records",
                            "generation_batches",
                            "generation_batch_items",
                            "agent_artifacts",
                            "asset_image_candidates",
                            "media_assets",
                            "project_render_jobs",
                            "project_scripts",
                            "media_files",
                            "canvas_binary_resources",
                            "novel_script_records",
                            "script_shot_records",
                            "user_creation_drafts",
                            "image_operations",
                        ]
                    ),
                    c.action.in_(["publish", "work_update"]),
                ),
            ),
            and_(c.project_id.is_(None), c.actor_user_id == user_id),
        )
    if name in {"email_outbox", "auth_rate_limits"}:
        return false()
    if "project_id" in c:
        return c.project_id.in_(project_ids(user_id))
    for field, parent in [
        ("shot_id", "shot_scripts"),
        ("assembly_id", "project_assemblies"),
        ("asset_id", "assets"),
        ("task_id", "async_tasks"),
        ("record_id", "ai_generation_records"),
        ("script_id", "project_scripts"),
        ("novel_id", "project_novels"),
        ("batch_id", "generation_batches"),
    ]:
        if field in c:
            return c[field].in_(allowed_ids(parent, user_id))
    # New resources must explicitly define their access path.
    return false()


def _recycle_condition(table, user_id: int, scope: CanvasRecycleScope | None):
    ordinary = condition(table, user_id)
    if scope is None:
        return ordinary
    allowed = project_ids(user_id, include_archived=True)
    if table.name == "projects":
        archived = and_(
            table.c.id == scope.project_id,
            table.c.id.in_(allowed),
            table.c.workspace_mode == "infinite_canvas",
        )
    elif table.name == "project_canvases":
        archived = and_(
            table.c.project_id == scope.project_id,
            table.c.id == scope.canvas_id,
            table.c.project_id.in_(allowed),
        )
    elif scope.include_document and table.name in {
        "canvas_nodes",
        "canvas_edges",
        "canvas_director_scenes",
        "canvas_timelines",
        "canvas_user_states",
        "canvas_node_user_states",
    }:
        archived = and_(
            table.c.project_id == scope.project_id,
            table.c.canvas_id == scope.canvas_id,
            table.c.project_id.in_(allowed),
            table.c.user_id == user_id if table.name in CANVAS_PRIVATE_TABLES else True,
        )
    elif scope.resource_id is not None and table.name in {"media_files", "canvas_binary_resources"}:
        archived = and_(
            table.c.id == scope.resource_id,
            table.c.project_id == scope.project_id,
            table.c.project_id.in_(allowed),
            or_(table.c.created_by == user_id, table.c.published_at.is_not(None)),
        )
    else:
        return ordinary
    return or_(ordinary, archived)


@lru_cache(maxsize=128)
def _scope_options(
    user_id: int,
    mappers: tuple[Mapper, ...],
    restore: CanvasRecycleScope | None = None,
    deletion_receipt_id: int | None = None,
    undo_asset_ids: tuple[int, ...] = (),
) -> tuple[ORMOption, ...]:
    # Reuse immutable SQL expressions, never membership or query results. The
    # database still checks current ownership, membership and archive state.
    return tuple(
        with_loader_criteria(
            mapper.class_,
            or_(
                _recycle_condition(mapper.local_table, user_id, restore),
                and_(
                    mapper.local_table.c.id == deletion_receipt_id,
                    mapper.local_table.c.owner_user_id == user_id,
                    mapper.local_table.c.scope_version == 3,
                    or_(
                        mapper.local_table.c.project_id.is_(None),
                        mapper.local_table.c.project_id.in_(project_ids(user_id)),
                    ),
                ),
            )
            if deletion_receipt_id is not None and mapper.local_table.name == "agent_conversations"
            else or_(
                _recycle_condition(mapper.local_table, user_id, restore),
                and_(
                    mapper.local_table.c.id.in_(undo_asset_ids),
                    mapper.local_table.c.project_id.in_(project_ids(user_id)),
                ),
            )
            if undo_asset_ids and mapper.local_table.name == "assets"
            else _recycle_condition(mapper.local_table, user_id, restore),
            include_aliases=True,
        )
        for mapper in mappers
    )


@event.listens_for(Session, "do_orm_execute")
def restrict_queries(state):
    from short_drama.db.agent_skill_access import guard_skill_bulk
    from short_drama.db.asset_version_access import guard_version_bulk

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
            from short_drama.domain.agent_task import AgentOperation

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


def _guard_memory_write(session, entity, actor, new):
    name = inspect(type(entity)).local_table.name
    if hasattr(entity, "owner_user_id"):
        if new:
            entity.owner_user_id = actor.user_id
        if entity.owner_user_id != actor.user_id:
            raise NotFound("记忆不存在")
    if hasattr(entity, "memory_id"):
        item = _agent_parent(session, "agent_memory_items", entity.memory_id)
        if item.owner_user_id != actor.user_id:
            raise NotFound("记忆不存在")
        if name == "agent_memory_forget_rules" and any(
            getattr(entity, field) != getattr(item, field)
            for field in ("scope_type", "scope_key", "kind", "memory_key")
        ):
            raise NotFound("遗忘规则不属于此记忆")
    if getattr(entity, "conversation_id", None) is not None:
        conversation = _agent_parent(session, "agent_conversations", entity.conversation_id)
        if conversation.owner_user_id != actor.user_id or conversation.scope_version != 3:
            raise NotFound("记忆来源会话不存在")
    if getattr(entity, "project_id", None) is not None:
        require_project(session, entity.project_id)
    if name == "agent_memory_sources" and entity.message_id is not None:
        message = _agent_parent(session, "agent_messages", entity.message_id)
        if message.conversation_id != entity.conversation_id or message.seq != entity.source_seq:
            raise NotFound("记忆来源不属于此会话")
    if name == "agent_memory_jobs" and entity.run_id is not None:
        run = _agent_parent(session, "agent_runs", entity.run_id)
        if run.conversation_id != entity.conversation_id:
            raise NotFound("记忆任务来源无效")
    immutable = name in {"agent_memory_revisions", "agent_memory_sources"}
    frozen = {
        "owner_user_id",
        "memory_id",
        "scope_type",
        "scope_key",
        "conversation_id",
        "project_id",
        "memory_key",
        "kind",
    }
    if not new and any(
        (immutable or field in frozen) and attr.history.has_changes()
        for field, attr in inspect(entity).attrs.items()
    ):
        raise WorkflowError("memory_source_immutable", "记忆身份及历史来源不能改写", 403)


def _guard_agent_write(session, entity, actor, new):
    name = inspect(type(entity)).local_table.name
    if name in {
        "agent_memory_items",
        "agent_memory_revisions",
        "agent_memory_sources",
        "agent_memory_forget_rules",
        "agent_conversation_summaries",
        "agent_memory_jobs",
    }:
        _guard_memory_write(session, entity, actor, new)
        return
    if name == "agent_preferences":
        if entity.owner_user_id != actor.user_id:
            raise NotFound("Resource does not exist")
        return
    if name in AGENT_PRIVATE_TABLES:
        if new and name == "agent_conversations":
            entity.owner_user_id = actor.user_id
        conversation = _agent_conversation(session, entity)
        if conversation.owner_user_id != actor.user_id:
            raise NotFound("Resource does not exist")
        if conversation.scope_version == 3:
            if conversation.project_id is not None:
                require_project(session, conversation.project_id)
            if any(
                getattr(conversation, field) is not None
                for field in ("stage", "subject_type", "subject_id", "task_type")
            ):
                raise WorkflowError("assistant_scope_invalid", "助手对话范围无效", 403)
        elif conversation.scope_version == 2:
            require_project(session, conversation.project_id)
            if any(
                getattr(conversation, field) is not None
                for field in ("stage", "subject_type", "subject_id", "task_type")
            ):
                raise WorkflowError("assistant_scope_invalid", "项目对话范围无效", 403)
            if name == "agent_tool_calls":
                raise WorkflowError("assistant_tools_forbidden", "创作助手只提供对话建议", 403)
        else:
            require_project(session, conversation.project_id)
            creation = _agent_parent(session, "projects", conversation.project_id)
            if creation.id != conversation.project_id:
                raise NotFound("Project does not belong to this project")
        if name == "agent_runs":
            if new and entity.initiated_by is None:
                entity.initiated_by = actor.user_id
            if entity.initiated_by != actor.user_id:
                raise NotFound("Run initiator does not own this conversation")
            if new:
                _agent_parent(session, "ai_model_configs", entity.model_config_id)
        if name in {"agent_messages", "agent_runs"} and entity.task_id is not None:
            task = _agent_parent(session, "agent_tasks", entity.task_id)
            if task.conversation_id != conversation.id or conversation.scope_version != 3:
                raise NotFound("Task does not belong to this conversation")
        if name == "agent_runs" and conversation.scope_version == 3:
            trigger = _agent_parent(session, "agent_messages", entity.trigger_message_id)
            if entity.task_id is None or trigger.task_id != entity.task_id:
                raise NotFound("Run trigger does not belong to this task")
        if name in {"agent_task_inputs", "agent_task_controls"}:
            message_id = (
                entity.message_id if name == "agent_task_inputs" else entity.source_message_id
            )
            if message_id is not None:
                message = _agent_parent(session, "agent_messages", message_id)
                if message.task_id != entity.task_id:
                    raise NotFound("Input does not belong to this task")
        if name == "agent_operations":
            run = _agent_parent(session, "agent_runs", entity.run_id)
            call = _agent_parent(session, "agent_tool_calls", entity.tool_call_id)
            if run.task_id != entity.task_id or call.run_id != run.id:
                raise NotFound("Operation does not belong to this task and run")
        if name == "agent_batch_undos":
            operation = _agent_parent(session, "agent_operations", entity.operation_id)
            task = _agent_parent(session, "agent_tasks", entity.task_id)
            if operation.task_id != task.id or task.conversation_id != conversation.id:
                raise NotFound("Undo does not belong to this task and operation")
            if not new and any(attr.history.has_changes() for attr in inspect(entity).attrs):
                raise WorkflowError("agent_record_immutable", "撤销回执不能改写", 403)
        if name == "agent_discussion_revisions":
            source = _agent_parent(session, "agent_messages", entity.source_message_id)
            reply = _agent_parent(session, "agent_messages", entity.reply_message_id)
            run = _agent_parent(session, "agent_runs", entity.run_id)
            if (
                source.conversation_id != conversation.id
                or reply.conversation_id != conversation.id
                or run.conversation_id != conversation.id
                or source.role != "user"
                or reply.role != "assistant"
                or source.task_id != run.task_id
                or reply.task_id != run.task_id
            ):
                raise NotFound("Discussion revision does not belong to this task")
            if not new and any(attr.history.has_changes() for attr in inspect(entity).attrs):
                raise WorkflowError("agent_record_immutable", "历史修订不能改写", 403)
        if name == "agent_media_requests":
            operation = _agent_parent(session, "agent_operations", entity.operation_id)
            if (
                operation.task_id != entity.task_id
                or operation.tool_name != "submit_generation_batch"
            ):
                raise NotFound("Media intent does not belong to this operation")
            if entity.generation_task_id is not None:
                generation = _agent_parent(session, "async_tasks", entity.generation_task_id)
                if generation.initiated_by != actor.user_id or str(generation.project_id) != str(
                    entity.frozen_target.get("project_id")
                ):
                    raise NotFound("Media generation does not belong to this intent")
            history = inspect(entity).attrs.generation_task_id.history
            if (
                not new
                and history.has_changes()
                and any(value is not None for value in history.deleted)
            ):
                raise WorkflowError(
                    "agent_record_immutable", "Media task identity cannot be replaced", 403
                )
        if name == "agent_task_controls":
            for field in ("requested_after_operation_id", "applied_after_operation_id"):
                operation_id = getattr(entity, field)
                if operation_id is not None:
                    operation = _agent_parent(session, "agent_operations", operation_id)
                    if operation.task_id != entity.task_id:
                        raise NotFound("Control boundary does not belong to this task")
        immutable = {
            "owner_user_id",
            "project_id",
            "stage",
            "subject_type",
            "subject_id",
            "task_type",
            "scope_version",
            "conversation_id",
            "run_id",
            "turn_id",
            "trigger_message_id",
            "trigger_revision",
            "initiated_by",
            "model_config_id",
            "task_id",
        }
        if not new and any(
            field in inspect(type(entity)).local_table.c
            and inspect(entity).attrs[field].history.has_changes()
            for field in immutable
        ):
            raise WorkflowError("ownership_immutable", "Agent ownership cannot be changed", 403)
        frozen = {
            "agent_media_requests": {
                "operation_id",
                "item_index",
                "media_kind",
                "requested_count",
                "frozen_target",
                "frozen_request",
                "created_at",
            },
            "agent_messages": {
                "content",
                "role",
                "seq",
                "references",
                "artifacts",
                "idempotency_key",
                "request_hash",
                "created_at",
                "row_version",
            },
            "agent_tool_calls": {
                "arguments",
                "arguments_hash",
                "idempotency_key",
                "provider_call_id",
                "call_index",
                "tool_name",
                "created_at",
            },
            "agent_events": {"seq", "event_type", "payload", "created_at"},
            "agent_task_inputs": {"message_id", "input_revision", "payload", "created_at"},
            "agent_task_controls": {
                "seq",
                "kind",
                "request_key",
                "request_hash",
                "expected_control_seq",
                "source_message_id",
                "requested_after_operation_id",
                "created_at",
            },
            "agent_operations": {
                "tool_call_id",
                "tool_name",
                "tool_version",
                "plan_revision",
                "input_revision",
                "payload_hash",
                "arguments",
                "created_at",
            },
        }.get(name, set())
        if name == "agent_messages" and session.info.get("assistant_discussion_edit") == entity.id:
            version = inspect(entity).attrs.row_version.history
            if (
                entity.role == "user"
                and version.deleted
                and version.added
                and version.added[0] == version.deleted[0] + 1
            ):
                frozen = frozen - {"content", "row_version"}
        if not new and any(inspect(entity).attrs[field].history.has_changes() for field in frozen):
            raise WorkflowError("agent_record_immutable", "Append a new Agent record", 403)
    elif name == "agent_artifacts":
        if not new and entity.created_by != actor.user_id:
            raise NotFound("Resource does not exist")
        require_project(session, entity.project_id)
        creation = _agent_parent(session, "projects", entity.project_id)
        if creation.id != entity.project_id:
            raise NotFound("Project does not belong to this project")
        if new:
            tool = _agent_parent(session, "agent_tool_calls", entity.tool_call_id)
            conversation = _agent_conversation(session, tool)
            if conversation.project_id != entity.project_id:
                raise NotFound("Artifact origin does not belong to this creation")
            _guard_artifact_references(session, entity)
        else:
            immutable = {
                "project_id",
                "tool_call_id",
                "result_index",
                "kind",
                "source_snapshot",
                "source_content",
                "proposed_patch",
                "script_id",
                "parent_script_id",
                "generation_task_id",
                "media_asset_id",
                "media_id",
                "target_asset_id",
                "target_shot_id",
                "created_by",
            }
            if any(inspect(entity).attrs[field].history.has_changes() for field in immutable):
                raise WorkflowError(
                    "agent_artifact_immutable", "Create a new candidate to change its source", 403
                )


def _guard_artifact_references(session, artifact):
    for field, table in (
        ("script_id", "project_scripts"),
        ("parent_script_id", "project_scripts"),
        ("target_shot_id", "shot_scripts"),
    ):
        identifier = getattr(artifact, field)
        if identifier:
            parent = _agent_parent(session, table, identifier)
            if parent.project_id != artifact.project_id:
                raise NotFound("Artifact reference is outside this creation")
    for field, table in (
        ("target_asset_id", "assets"),
        ("generation_task_id", "async_tasks"),
        ("media_asset_id", "media_assets"),
        ("media_id", "media_files"),
    ):
        identifier = getattr(artifact, field)
        if identifier:
            parent = _agent_parent(session, table, identifier)
            if scope_of(session, parent) != (None, artifact.project_id):
                raise NotFound("Artifact reference is outside this project")
    if artifact.media_asset_id:
        media = _agent_parent(session, "media_assets", artifact.media_asset_id)
        record = _agent_parent(session, "ai_generation_records", media.record_id)
        if artifact.media_id and artifact.media_id != media.media_id:
            raise NotFound("Artifact media references do not match")
        if artifact.generation_task_id and artifact.generation_task_id != record.task_id:
            raise NotFound("Artifact generation references do not match")
    if artifact.generation_task_id:
        from short_drama.domain import AIGenerationRecord

        record = session.scalar(
            select(AIGenerationRecord)
            .where(
                AIGenerationRecord.task_id == artifact.generation_task_id,
            )
            .order_by(AIGenerationRecord.call_no)
            .limit(1)
        )
        if record:
            project_id = (record.request_data.get("source") or {}).get("project_id")
            if project_id and str(artifact.project_id) != str(project_id):
                raise NotFound("Artifact generation is outside this creation")


def set_scope(session, scope):
    session.info["resource_scope"] = scope


def scoped_key(session, raw):
    """Opaque fixed-size key preserves existing storage limits and old system tests."""
    import hashlib

    actor = session.info.get("actor")
    if not actor:
        return raw
    return hashlib.sha256(f"{actor.user_id}:{raw}".encode()).hexdigest()


def _canvas_snapshot_reads(session, actor):
    from short_drama.domain import (
        CanvasBinaryResource,
        CanvasUserBinaryReference,
        MediaFile,
        Project,
        ProjectCanvas,
    )
    from short_drama.domain.canvas import CanvasUserMediaReference

    references = {
        entity
        for entity in session.new
        if isinstance(entity, (CanvasUserMediaReference, CanvasUserBinaryReference))
        and entity.owner_kind == "revision"
        and entity.revision_id is not None
        and entity.user_id == actor.user_id
    }
    if not references:
        return set(), {}
    # Mixed parent/resource edits retain the original per-entity checks. The
    # maps below exist only for this flush, never as cached authorization.
    related = (Project, ProjectCanvas, MediaFile, CanvasBinaryResource)
    if (
        any(isinstance(entity, Project) for entity in session.new)
        or any(isinstance(entity, related) for entity in session.deleted)
        or any(
            isinstance(entity, related) and session.is_modified(entity, include_collections=False)
            for entity in session.dirty
        )
    ):
        return set(), {}
    project_ids = {entity.project_id for entity in references}
    # Match the existing project/member -> resource lock order. These locks
    # remain held while every reference's ownership and scope are checked.
    for identifier in sorted(project_ids):
        require_project(session, identifier)
    reads = {}
    for model, identifiers in (
        (Project, project_ids),
        (ProjectCanvas, {entity.canvas_id for entity in references}),
        (
            MediaFile,
            {
                entity.media_id
                for entity in references
                if isinstance(entity, CanvasUserMediaReference)
            },
        ),
        (
            CanvasBinaryResource,
            {
                entity.binary_id
                for entity in references
                if isinstance(entity, CanvasUserBinaryReference)
            },
        ),
    ):
        values = {
            entity.id: entity
            for entity in session.new
            if isinstance(entity, model) and entity.id in identifiers
        }
        missing = sorted(identifiers - values.keys())
        for offset in range(0, len(missing), 500):
            statement = (
                select(model).where(model.id.in_(missing[offset : offset + 500])).order_by(model.id)
            )
            if model in (MediaFile, CanvasBinaryResource):
                statement = statement.with_for_update(read=True)
            values.update((entity.id, entity) for entity in session.scalars(statement))
        reads[model] = values
    return references, reads


def _guard_canvas_write(session, entity, actor, new, *, snapshot_reads=None):
    from short_drama.domain import Project, ProjectCanvas

    name = inspect(type(entity)).local_table.name
    if name in {
        "canvas_style_profiles",
        "canvas_style_media_references",
        "canvas_style_preferences",
    }:
        from .canvas_style_access import guard_canvas_style

        guard_canvas_style(session, entity, actor, new)
        return
    if name == "canvas_local_jobs":
        from .canvas_local_access import guard_canvas_local

        guard_canvas_local(session, entity, actor, new)
        return
    if name in {"canvas_render_jobs", "canvas_render_media_references"}:
        from .canvas_render_access import guard_canvas_render

        guard_canvas_render(session, entity, actor, new)
        return
    if name in {"canvas_tool_runs", "canvas_tool_submissions"}:
        from .canvas_tool_run_access import guard_canvas_tool_run

        guard_canvas_tool_run(session, entity, actor, new)
        return
    if name in {"canvas_task_bindings", "canvas_task_media_references", "canvas_results"}:
        from .canvas_generation_access import guard_canvas_generation

        guard_canvas_generation(session, entity, actor, new)
        return
    if name in {"canvas_project_folders", "canvas_project_folder_items"}:
        from .canvas_folder_access import guard_canvas_folder

        guard_canvas_folder(session, entity, actor, new)
        return
    if name in {"canvas_creation_attempts", "canvas_creation_resources"}:
        from .canvas_creation_access import guard_canvas_creation

        guard_canvas_creation(session, entity, actor, new)
        return
    if name in {"canvas_library_asset_references", "canvas_library_folder_items"}:
        _canvas_library_parent(session, entity.library_asset_id)
        if not new and inspect(entity).attrs.library_asset_id.history.has_changes():
            raise WorkflowError("ownership_immutable", "Library ownership is fixed", 403)
        if name == "canvas_library_folder_items" and entity not in session.deleted:
            from short_drama.domain import CanvasLibraryFolder

            folder = session.scalar(
                select(CanvasLibraryFolder).where(CanvasLibraryFolder.id == entity.folder_id)
            )
            if folder is None or folder.user_id != actor.user_id:
                raise NotFound("Canvas library folder does not exist")
        return
    if name in {"canvas_resource_chunks", "canvas_resource_copy_sources"}:
        parent = _canvas_upload_parent(session, entity.upload_id)
        if not new and inspect(entity).attrs.upload_id.history.has_changes():
            raise WorkflowError("ownership_immutable", "Upload ownership is fixed", 403)
        if name == "canvas_resource_copy_sources":
            if parent.mode != "copy":
                raise NotFound("Copy provenance requires a copy upload")
            if parent.status == "ready" and (
                entity.source_media_id is not None
                or entity.source_binary_id is not None
                or entity.released_at is None
            ):
                raise WorkflowError("ownership_immutable", "Completed copy source is released", 403)
            if not new and any(
                inspect(entity).attrs[field].history.has_changes()
                for field in ("original_resource_id", "snapshot_json", "created_at")
            ):
                raise WorkflowError("ownership_immutable", "Copy provenance is fixed", 403)
            from short_drama.domain import CanvasBinaryResource, MediaFile

            for field, model in (
                ("source_media_id", MediaFile),
                ("source_binary_id", CanvasBinaryResource),
            ):
                identifier = getattr(entity, field)
                if identifier is not None and (
                    identifier != entity.original_resource_id
                    or session.scalar(select(model.id).where(model.id == identifier)) is None
                ):
                    raise NotFound("Copy source does not exist")
        return
    if name in CANVAS_PRIVATE_TABLES:
        field = "actor_user_id" if name == "canvas_write_receipts" else "user_id"
        if getattr(entity, field) != actor.user_id:
            raise NotFound("Canvas state does not exist")
        if not new:
            if name == "canvas_write_receipts" and (
                entity in session.deleted or session.is_modified(entity, include_collections=False)
            ):
                raise WorkflowError("canvas_receipt_immutable", "写入回执不能改写或删除", 403)
            if inspect(entity).attrs[field].history.has_changes():
                raise WorkflowError("ownership_immutable", "Canvas state ownership is fixed", 403)
            # The service may have waited for a project lock after an earlier
            # REPEATABLE READ snapshot. Recheck the current owned row, not that
            # snapshot, or a just-created personal state becomes a false 404.
            if (
                session.scalar(
                    select(type(entity).id).where(type(entity).id == entity.id).with_for_update()
                )
                is None
            ):
                raise NotFound("Canvas state does not exist")
    if name == "canvas_resource_deletions" and not new:
        raise WorkflowError("canvas_deletion_immutable", "Deletion receipts are immutable", 403)
    if (
        name in {"canvas_drawing_versions", "canvas_revision_drawing_references"}
        and not new
        and entity not in session.deleted
        and session.is_modified(entity, include_collections=False)
    ):
        raise WorkflowError(
            "canvas_drawing_version_immutable", "已保存绘图和历史绑定不能原地改写", 403
        )
    if name in {
        "canvas_workspace_user_states",
        "canvas_model_catalogs",
        "canvas_channel_models",
        "canvas_library_folders",
        "canvas_resource_deletions",
    }:
        if name == "canvas_channel_models":
            config = next(
                (
                    item
                    for item in session.new
                    if isinstance(item, AIModelConfig) and item.id == entity.model_config_id
                ),
                None,
            )
            if config is None:
                config = session.scalar(
                    select(AIModelConfig).where(AIModelConfig.id == entity.model_config_id)
                )
            if config is None or config.owner_user_id != actor.user_id:
                raise NotFound("本人渠道执行配置不存在")
            if not new and any(
                inspect(entity).attrs[field].history.has_changes()
                for field in ("channel_key", "model_key", "model_config_id")
            ):
                raise WorkflowError("ownership_immutable", "渠道模型身份不可修改", 403)
        return
    if name == "canvas_library_assets" and entity.project_id is None:
        return
    if name in {"canvas_binary_resources", "canvas_resource_uploads"} and entity.project_id is None:
        if entity.scope_user_id != actor.user_id:
            raise NotFound("Canvas resource does not exist")
        return
    if snapshot_reads is not None:
        project = snapshot_reads[Project].get(entity.project_id)
    else:
        project = next(
            (x for x in session.new if isinstance(x, Project) and x.id == entity.project_id), None
        )
        if project is None:
            project = session.scalar(select(Project).where(Project.id == entity.project_id))
    if project is None or project.workspace_mode != "infinite_canvas":
        raise NotFound("Infinite canvas project does not exist")
    canvas_id = getattr(entity, "canvas_id", None) or getattr(entity, "primary_canvas_id", None)
    if canvas_id:
        if snapshot_reads is not None:
            canvas = snapshot_reads[ProjectCanvas].get(canvas_id)
        else:
            canvas = next(
                (x for x in session.new if isinstance(x, ProjectCanvas) and x.id == canvas_id), None
            )
            if canvas is None:
                canvas = session.scalar(select(ProjectCanvas).where(ProjectCanvas.id == canvas_id))
        if canvas is None or canvas.project_id != entity.project_id:
            raise NotFound("Canvas is outside this project")
    if not new:
        for field in (
            "canvas_id",
            "source_key",
            "workspace_key",
            "node_key",
            "edge_key",
            "revision_id",
        ):
            if (
                field in inspect(type(entity)).local_table.c
                and inspect(entity).attrs[field].history.has_changes()
            ):
                raise WorkflowError("ownership_immutable", "Canvas record identity is fixed", 403)


@event.listens_for(Session, "before_flush")
def guard_writes(session, _context, _instances):
    from short_drama.db.agent_skill_access import guard_skill_versions
    from short_drama.db.asset_version_access import guard_version_writes

    guard_version_writes(session)
    guard_skill_versions(session)
    actor = session.info.get("actor")
    legacy_user = session.info.get("legacy_user_id")
    if not actor and not legacy_user:
        return
    user_id = actor.user_id if actor else legacy_user
    scope = session.info.get("resource_scope", (user_id, session.info.get("request_project")))
    if scope[1]:
        scope = (None, scope[1])
    changed = (
        list(session.new)
        + [x for x in session.dirty if session.is_modified(x, include_collections=False)]
        + list(session.deleted)
    )
    snapshot_references, snapshot_reads = (
        _canvas_snapshot_reads(session, actor) if actor else (set(), {})
    )
    for entity in changed:
        table = inspect(type(entity)).local_table
        name = table.name
        if name in {
            "users",
            "user_sessions",
            "email_challenges",
            "email_outbox",
            "auth_rate_limits",
            "audit_events",
            "user_model_preferences",
            "user_project_states",
        }:
            continue
        new = entity in session.new
        if actor and name == "user_creation_drafts":
            if entity.user_id != actor.user_id:
                raise NotFound("私人创作草稿不存在")
            if not new and any(
                inspect(entity).attrs[field].history.has_changes()
                for field in ("user_id", "project_id", "object_id", "purpose")
            ):
                raise WorkflowError("ownership_immutable", "私人草稿所属和用途不可修改", 403)
        if actor and name in {"agent_skills", "agent_attachments"}:
            if entity.owner_user_id != actor.user_id:
                raise NotFound("Resource does not exist")
            if name == "agent_attachments":
                conversation = _agent_parent(session, "agent_conversations", entity.conversation_id)
                if conversation.owner_user_id != actor.user_id:
                    raise NotFound("Resource does not exist")
                if entity.attached_message_id:
                    message = _agent_parent(session, "agent_messages", entity.attached_message_id)
                    if message.conversation_id != entity.conversation_id:
                        raise NotFound("Attachment message is outside this conversation")
            if not new and any(
                field in table.c and inspect(entity).attrs[field].history.has_changes()
                for field in ("owner_user_id", "conversation_id")
            ):
                raise WorkflowError(
                    "ownership_immutable", "Resource ownership cannot be changed", 403
                )
        if actor and name in AGENT_TABLES:
            _guard_agent_write(session, entity, actor, new)
        if actor and name in CANVAS_TABLES:
            _guard_canvas_write(
                session,
                entity,
                actor,
                new,
                snapshot_reads=snapshot_reads if entity in snapshot_references else None,
            )
        if new:
            if name in {"projects", "ai_model_configs"}:
                entity.owner_user_id = user_id
            elif (
                isinstance(entity, ResourceScope)
                and entity.scope_user_id is None
                and entity.project_id is None
            ):
                entity.scope_user_id, entity.project_id = scope
            if hasattr(entity, "initiated_by") and entity.initiated_by is None:
                entity.initiated_by = user_id
        if not actor:
            continue
        if not new and name in {
            "async_tasks",
            "generation_batches",
            "generation_batch_items",
            "project_render_jobs",
            "ai_generation_records",
            "media_assets",
            "novel_script_records",
            "script_shot_records",
            "asset_image_candidates",
            "project_scripts",
            "media_files",
            "agent_artifacts",
            "canvas_binary_resources",
            "image_operations",
        }:
            # Session.get may return an object already in the identity map. Verify
            # the stored ownership before accepting writes to private resources.
            query = select(type(entity).id).where(type(entity).id == entity.id)
            if name == "media_files":
                query = query.with_for_update()
            if session.scalar(query) is None:
                raise NotFound("Resource does not exist")
        if name in {"project_scripts", "media_files", "canvas_binary_resources"} and not new:
            publication = inspect(entity).attrs.published_at.history
            if publication.has_changes() and publication.deleted and publication.deleted[0]:
                raise WorkflowError(
                    "publication_immutable", "Published works cannot be unpublished", 403
                )
        entity_scope = scope_of(session, entity)
        if entity_scope[1] and not (new and name == "projects"):
            recycle = recycle_scope(session)
            disposal = (
                new
                and name == "canvas_write_receipts"
                and recycle is not None
                and recycle.disposal_receipt
                and entity.project_id == recycle.project_id
                and entity.canvas_id == recycle.canvas_id
                and entity.operation_kind == "canvas.recycle.purge"
            )
            # Only this new private acknowledgement may be written after its
            # parent was archived. Scoped parent reads above still check membership.
            if not disposal and entity not in snapshot_references:
                require_project(session, entity_scope[1])
        elif not entity_scope[1] and entity_scope[0] != actor.user_id:
            raise NotFound("Resource does not exist")
        if name == "projects" and entity in session.deleted:
            require_project(session, entity.id, owner=True)
        # Ownership is immutable through normal edits.
        for field in ("owner_user_id", "scope_user_id", "project_id", "initiated_by", "created_by"):
            if not new and field in table.c and inspect(entity).attrs[field].history.has_changes():
                raise WorkflowError(
                    "ownership_immutable", "Resource ownership cannot be changed", 403
                )
        frozen = {
            "image_groups": {"asset_id", "shot_id", "task_id", "source_context", "expected_count"},
            "image_group_items": {"group_id", "output_index"},
            "image_operations": {
                "item_id",
                "source_media_id",
                "source_version",
                "operation_type",
                "parameters",
                "request_key",
                "request_hash",
            },
        }.get(name, set())
        if not new and any(inspect(entity).attrs[field].history.has_changes() for field in frozen):
            raise WorkflowError("image_source_immutable", "图片处理来源不可改写", 403)
        for field, target_name in [
            ("media_id", "media_files"),
            ("first_frame_media_id", "media_files"),
            ("last_frame_media_id", "media_files"),
            ("output_media_id", "media_files"),
            ("current_media_id", "media_files"),
            ("proxy_media_id", "media_files"),
            ("asset_id", "assets"),
            ("binary_id", "canvas_binary_resources"),
            *([("shot_id", "shot_scripts")] if name == "image_groups" else []),
            *([("group_id", "image_groups")] if name == "image_group_items" else []),
            *(
                [
                    ("item_id", "image_group_items"),
                    ("source_media_id", "media_files"),
                    ("result_media_id", "media_files"),
                    ("mask_media_id", "media_files"),
                    ("result_group_id", "image_groups"),
                ]
                if name == "image_operations"
                else []
            ),
        ]:
            if field not in table.c or not getattr(entity, field, None):
                continue
            model = next(
                m.class_ for m in Base.registry.mappers if m.local_table.name == target_name
            )
            identifier = int(getattr(entity, field))
            if entity in snapshot_references:
                target = snapshot_reads[model].get(identifier)
            else:
                target = next(
                    (x for x in session.new if isinstance(x, model) and x.id == identifier), None
                )
                if target is None:
                    # 任务当前读可能已看到Worker在本事务快照之后归档的媒体。
                    # 引用校验同样读取当前行，并保留作者、项目和资源范围条件。
                    target = session.scalar(
                        select(model).where(model.id == identifier).with_for_update(read=True)
                    )
            allowed_scopes = {entity_scope}
            if (
                name == "canvas_style_media_references"
                and entity_scope[0] == actor.user_id
                and target is not None
            ):
                allowed_scopes.add(scope_of(session, target))
            if name == "agent_attachments" and field == "media_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_user_media_references" and field == "media_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_render_media_references" and field == "media_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_local_jobs" and field == "output_media_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_user_binary_references" and field == "binary_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_library_asset_references":
                allowed_scopes.add((actor.user_id, None))
                if target is not None and scope_of(session, target) not in allowed_scopes:
                    from short_drama.utils.canvas_document import media_references

                    parent = _canvas_library_parent(session, entity.library_asset_id)
                    original_ids = {value for _, value in media_references(parent.payload_json)}
                    if entity in session.deleted:
                        for previous in inspect(parent).attrs.payload_json.history.deleted:
                            original_ids.update(value for _, value in media_references(previous))
                    ancestors = copy_ancestors(session, {identifier})
                    if ancestors.get(identifier, set()).intersection(original_ids):
                        # Only an owned, completed, provenance-verified copy may cross
                        # the private asset's project boundary. Ordinary refs stay scoped.
                        allowed_scopes.add(scope_of(session, target))
            if target is None or scope_of(session, target) not in allowed_scopes:
                raise NotFound("Reference is outside this resource scope")
            if (
                name
                in {
                    "canvas_media_references",
                    "canvas_revision_media_references",
                    "canvas_binary_references",
                }
                and target.published_at is None
            ):
                raise NotFound("Shared canvas media must be published project work")
        if hasattr(entity, "reference_media_ids"):
            from short_drama.domain import MediaFile

            for identifier in entity.reference_media_ids or []:
                target = session.scalar(
                    select(MediaFile).where(MediaFile.id == int(identifier)).with_for_update()
                )
                allowed_scopes = {entity_scope}
                if name == "user_creation_drafts":
                    allowed_scopes.add((actor.user_id, None))
                if target is None or scope_of(session, target) not in allowed_scopes:
                    raise NotFound("Reference image is outside this resource scope")
        if hasattr(entity, "created_by") and new:
            entity.created_by = actor.user_id
        if hasattr(entity, "updated_by"):
            entity.updated_by = actor.user_id
        # A project audit is shared. Private chat/object identifiers belong only
        # in the conversation's own event log, never in this public timeline.
        if name in AGENT_PRIVATE_TABLES | CANVAS_PRIVATE_TABLES or name in {
            "agent_attachments",
            "agent_skills",
            "agent_skill_versions",
            "agent_skill_settings",
            "user_creation_drafts",
            "image_operations",
        }:
            continue
        action = "create" if new else "delete" if entity in session.deleted else "update"
        if (
            name in {"project_scripts", "media_files", "canvas_binary_resources"}
            and entity.published_at is not None
        ):
            action = (
                "publish"
                if inspect(entity).attrs.published_at.history.has_changes()
                else "work_update"
            )
        session.info.setdefault("pending_audit", []).append(
            AuditEvent(
                id=next_id(),
                actor_user_id=actor.user_id,
                project_id=entity_scope[1],
                object_type=name,
                object_id=str(getattr(entity, "id", getattr(entity, "project_id", ""))),
                action=action,
                request_id=actor.request_id,
                created_at=utcnow(),
            )
        )


@event.listens_for(Session, "after_flush_postexec")
def persist_audit(session, _context):
    for record in session.info.pop("pending_audit", []):
        session.add(record)


@event.listens_for(Session, "after_rollback")
def clear_audit(session):
    session.info.pop("pending_audit", None)
