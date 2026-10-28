from sqlalchemy import inspect, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.scope.resource_scope import (
    _agent_conversation,
    _agent_parent,
)
from short_drama.db.scope.resource_scope import (
    require_project as require_project,
)
from short_drama.db.scope.resource_scope import (
    scope_of as scope_of,
)
from short_drama.domain import (
    AGENT_PRIVATE_TABLES,
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
        if name in {"agent_graph_checkpoints", "agent_graph_writes"}:
            run = _agent_parent(session, "agent_runs", entity.run_id)
            if (
                entity.owner_user_id != actor.user_id
                or run.task_id != entity.task_id
                or run.initiated_by != actor.user_id
                or run.conversation_id != conversation.id
            ):
                raise NotFound("Graph record does not belong to this task and run")
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
