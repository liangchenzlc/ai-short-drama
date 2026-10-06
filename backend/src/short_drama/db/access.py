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


def project_ids(user_id, *, include_archived=False):
    projects = Base.metadata.tables["projects"].alias("allowed_projects")
    members = Base.metadata.tables["project_members"].alias("allowed_members")
    return select(projects.c.id).where(
        True if include_archived else projects.c.archived_at.is_(None),
        or_(
            projects.c.owner_user_id == user_id,
            projects.c.id.in_(
                select(members.c.project_id).where(
                    members.c.user_id == user_id, members.c.status == "active"
                )
            ),
        ),
    )


def allowed_ids(name, user_id):
    table = Base.metadata.tables[name].alias(f"allowed_{name}")
    return select(table.c.id).where(condition(table, user_id))


def condition(table, user_id):
    name = table.original.name if hasattr(table, "original") else table.name
    c = table.c
    if name in {
        "canvas_workspace_user_states",
        "canvas_model_catalogs",
        "canvas_channel_models",
        "canvas_beefapi_connections",
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
    # Private execution data never inherits the ordinary shared-project predicate.
    if name == "agent_conversations":
        return and_(c.owner_user_id == user_id, c.project_id.in_(project_ids(user_id)))
    if name in {"agent_messages", "agent_runs", "agent_events"}:
        return c.conversation_id.in_(allowed_ids("agent_conversations", user_id))
    if name in {"agent_turns", "agent_tool_calls"}:
        return c.run_id.in_(allowed_ids("agent_runs", user_id))
    if name == "agent_artifacts":
        return and_(c.created_by == user_id, c.project_id.in_(project_ids(user_id)))
    if name == "agent_skills":
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
    if name == "episode_render_jobs":
        return and_(
            c.initiated_by == user_id,
            c.assembly_id.in_(allowed_ids("episode_assemblies", user_id)),
        )
    if name == "episode_scripts":
        return and_(
            or_(c.created_by == user_id, c.published_at.is_not(None)),
            c.episode_id.in_(allowed_ids("episodes", user_id)),
        )
    if name in {"novel_script_records", "script_shot_records"}:
        return and_(
            c.created_by == user_id, c.script_id.in_(allowed_ids("episode_scripts", user_id))
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
        members = Base.metadata.tables["project_members"].alias("project_access_members")
        return and_(
            c.archived_at.is_(None),
            or_(
                c.owner_user_id == user_id,
                c.id.in_(
                    select(members.c.project_id).where(
                        members.c.user_id == user_id,
                        members.c.status == "active",
                    )
                ),
            ),
        )
    if name == "ai_model_configs":
        return c.owner_user_id == user_id
    if "scope_user_id" in c:
        return or_(c.scope_user_id == user_id, c.project_id.in_(project_ids(user_id)))
    if name == "users":
        return c.id == user_id
    if name == "global_assets":
        return c.user_id == user_id
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
                            "episode_render_jobs",
                            "episode_scripts",
                            "media_files",
                            "canvas_binary_resources",
                            "novel_script_records",
                            "script_shot_records",
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
        ("episode_id", "episodes"),
        ("shot_id", "shot_scripts"),
        ("assembly_id", "episode_assemblies"),
        ("asset_id", "assets"),
        ("task_id", "async_tasks"),
        ("record_id", "ai_generation_records"),
        ("script_id", "episode_scripts"),
        ("novel_id", "episode_novels"),
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
    elif table.name == "project_members":
        archived = and_(table.c.project_id == scope.project_id, table.c.project_id.in_(allowed))
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
    user_id: int, mappers: tuple[Mapper, ...], restore: CanvasRecycleScope | None = None
) -> tuple[ORMOption, ...]:
    # Reuse immutable SQL expressions, never membership or query results. The
    # database still checks current ownership, membership and archive state.
    return tuple(
        with_loader_criteria(
            mapper.class_,
            _recycle_condition(mapper.local_table, user_id, restore),
            include_aliases=True,
        )
        for mapper in mappers
    )


@event.listens_for(Session, "do_orm_execute")
def restrict_queries(state):
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
        state.statement = state.statement.options(
            *_scope_options(
                actor.user_id, tuple(Base.registry.mappers), recycle_scope(state.session)
            )
        )


def require_project(session, project_id, *, owner=False, lock=True):
    from short_drama.domain import Project
    from short_drama.domain.collaboration import ProjectMember

    actor = session.info.get("actor")
    if not actor:
        return
    statement = select(Project).where(Project.id == int(project_id))
    if lock:
        statement = statement.with_for_update()
    project = session.scalar(statement)
    if project is None or (
        project.archived_at and not inspect(project).attrs.archived_at.history.has_changes()
    ):
        raise NotFound("Project does not exist")
    if project.owner_user_id != actor.user_id:
        membership = select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == actor.user_id,
            ProjectMember.status == "active",
        )
        member = session.scalar(membership.with_for_update() if lock else membership)
        if not member:
            raise NotFound("Project does not exist")
        if owner:
            raise WorkflowError("project_owner_required", "Only the project owner can do this", 403)


def scope_of(session, entity):
    """Resolve the actual ownership path, independent of the caller's entry point."""
    table = inspect(type(entity)).local_table
    name = table.name
    if name in {"canvas_resource_chunks", "canvas_resource_copy_sources"}:
        return scope_of(session, _canvas_upload_parent(session, entity.upload_id))
    if name in {"canvas_library_asset_references", "canvas_library_folder_items"}:
        return scope_of(session, _canvas_library_parent(session, entity.library_asset_id))
    if name in {
        "canvas_library_folders",
        "canvas_project_folders",
        "canvas_project_folder_items",
        "canvas_resource_deletions",
        "canvas_creation_attempts",
        "canvas_creation_resources",
    }:
        return (entity.user_id, None)
    if name == "canvas_library_assets":
        return (None, entity.project_id) if entity.project_id else (entity.user_id, None)
    if name == "agent_skills":
        return (entity.owner_user_id, None)
    if name == "agent_attachments":
        return (
            None,
            _agent_parent(session, "agent_conversations", entity.conversation_id).project_id,
        )
    if name in AGENT_PRIVATE_TABLES:
        conversation = _agent_conversation(session, entity)
        return (None, conversation.project_id)
    if name == "projects":
        return (None, entity.id)
    if isinstance(entity, ResourceScope):
        return (entity.scope_user_id, entity.project_id)
    if name == "ai_model_configs":
        return (entity.owner_user_id, None)
    if name in {
        "global_assets",
        "canvas_workspace_user_states",
        "canvas_model_catalogs",
        "canvas_channel_models",
        "canvas_beefapi_connections",
    }:
        return (entity.user_id, None)
    if "project_id" in table.c and entity.project_id:
        return (None, entity.project_id)
    for field, parent in [
        ("episode_id", "episodes"),
        ("shot_id", "shot_scripts"),
        ("assembly_id", "episode_assemblies"),
        ("asset_id", "assets"),
        ("task_id", "async_tasks"),
        ("record_id", "ai_generation_records"),
        ("script_id", "episode_scripts"),
        ("novel_id", "episode_novels"),
        ("batch_id", "generation_batches"),
    ]:
        if field in table.c and getattr(entity, field, None):
            value = int(getattr(entity, field))
            model = next(m.class_ for m in Base.registry.mappers if m.local_table.name == parent)
            obj = next(
                (
                    x
                    for x in session.new
                    if isinstance(x, model) and getattr(x, "id", None) == value
                ),
                None,
            )
            if obj is None:
                obj = session.scalar(select(model).where(model.id == value))
            if obj is None:
                raise NotFound("Referenced resource does not exist")
            return scope_of(session, obj)
    return (None, None)


def _agent_parent(session, name, identifier):
    model = next(m.class_ for m in Base.registry.mappers if m.local_table.name == name)
    entity = next((x for x in session.new if isinstance(x, model) and x.id == identifier), None)
    if entity is None:
        entity = session.scalar(select(model).where(model.id == identifier))
    if entity is None:
        raise NotFound("Agent parent does not exist")
    return entity


def _agent_conversation(session, entity):
    name = inspect(type(entity)).local_table.name
    if name == "agent_conversations":
        return entity
    if name in {"agent_messages", "agent_runs", "agent_events"}:
        return _agent_parent(session, "agent_conversations", entity.conversation_id)
    if name == "agent_turns" or name == "agent_tool_calls":
        run = _agent_parent(session, "agent_runs", entity.run_id)
        return _agent_parent(session, "agent_conversations", run.conversation_id)
    raise NotFound("Agent parent does not exist")


def _guard_agent_write(session, entity, actor, new):
    name = inspect(type(entity)).local_table.name
    if name in AGENT_PRIVATE_TABLES:
        if new and name == "agent_conversations":
            entity.owner_user_id = actor.user_id
        conversation = _agent_conversation(session, entity)
        if conversation.owner_user_id != actor.user_id:
            raise NotFound("Resource does not exist")
        require_project(session, conversation.project_id)
        episode = _agent_parent(session, "episodes", conversation.episode_id)
        if episode.project_id != conversation.project_id:
            raise NotFound("Episode does not belong to this project")
        if name == "agent_runs":
            if new and entity.initiated_by is None:
                entity.initiated_by = actor.user_id
            if entity.initiated_by != actor.user_id:
                raise NotFound("Run initiator does not own this conversation")
            if new:
                _agent_parent(session, "ai_model_configs", entity.model_config_id)
        immutable = {
            "owner_user_id",
            "project_id",
            "episode_id",
            "stage",
            "subject_type",
            "subject_id",
            "task_type",
            "scope_version",
            "conversation_id",
            "run_id",
            "turn_id",
            "trigger_message_id",
            "initiated_by",
            "model_config_id",
        }
        if not new and any(
            field in inspect(type(entity)).local_table.c
            and inspect(entity).attrs[field].history.has_changes()
            for field in immutable
        ):
            raise WorkflowError("ownership_immutable", "Agent ownership cannot be changed", 403)
        frozen = {
            "agent_messages": {
                "content",
                "role",
                "seq",
                "references",
                "artifacts",
                "idempotency_key",
                "request_hash",
                "created_at",
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
        }.get(name, set())
        if not new and any(inspect(entity).attrs[field].history.has_changes() for field in frozen):
            raise WorkflowError("agent_record_immutable", "Append a new Agent record", 403)
    elif name == "agent_artifacts":
        if not new and entity.created_by != actor.user_id:
            raise NotFound("Resource does not exist")
        require_project(session, entity.project_id)
        episode = _agent_parent(session, "episodes", entity.episode_id)
        if episode.project_id != entity.project_id:
            raise NotFound("Episode does not belong to this project")
        if new:
            tool = _agent_parent(session, "agent_tool_calls", entity.tool_call_id)
            conversation = _agent_conversation(session, tool)
            if (conversation.project_id, conversation.episode_id) != (
                entity.project_id,
                entity.episode_id,
            ):
                raise NotFound("Artifact origin does not belong to this episode")
            _guard_artifact_references(session, entity)
        else:
            immutable = {
                "project_id",
                "episode_id",
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
        ("script_id", "episode_scripts"),
        ("parent_script_id", "episode_scripts"),
        ("target_shot_id", "shot_scripts"),
    ):
        identifier = getattr(artifact, field)
        if identifier:
            parent = _agent_parent(session, table, identifier)
            if parent.episode_id != artifact.episode_id:
                raise NotFound("Artifact reference is outside this episode")
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
    if artifact.target_asset_id:
        from short_drama.domain import EpisodeAsset

        linked = next(
            (
                x
                for x in session.new
                if isinstance(x, EpisodeAsset)
                and x.episode_id == artifact.episode_id
                and x.asset_id == artifact.target_asset_id
            ),
            None,
        )
        if linked is None:
            linked = session.scalar(
                select(EpisodeAsset).where(
                    EpisodeAsset.episode_id == artifact.episode_id,
                    EpisodeAsset.asset_id == artifact.target_asset_id,
                )
            )
        if linked is None:
            raise NotFound("Artifact asset is not linked to this episode")
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
            episode_id = (record.request_data.get("source") or {}).get("episode_id")
            if episode_id and str(artifact.episode_id) != str(episode_id):
                raise NotFound("Artifact generation is outside this episode")


def set_scope(session, scope):
    session.info["resource_scope"] = scope


def scoped_key(session, raw):
    """Opaque fixed-size key preserves existing storage limits and old system tests."""
    import hashlib

    actor = session.info.get("actor")
    if not actor:
        return raw
    return hashlib.sha256(f"{actor.user_id}:{raw}".encode()).hexdigest()


def _canvas_library_parent(session, identifier):
    from short_drama.domain import CanvasLibraryAsset

    parent = next(
        (x for x in session.new if isinstance(x, CanvasLibraryAsset) and x.id == identifier), None
    )
    if parent is None:
        parent = session.scalar(
            select(CanvasLibraryAsset).where(CanvasLibraryAsset.id == identifier)
        )
    if parent is None:
        raise NotFound("Canvas library asset does not exist")
    return parent


def _canvas_upload_parent(session, identifier):
    from short_drama.domain import CanvasResourceUpload

    parent = next(
        (x for x in session.new if isinstance(x, CanvasResourceUpload) and x.id == identifier),
        None,
    )
    if parent is None:
        parent = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.id == identifier)
        )
    if parent is None:
        raise NotFound("Canvas upload does not exist")
    return parent


def _guard_canvas_write(session, entity, actor, new):
    from short_drama.domain import Project, ProjectCanvas

    name = inspect(type(entity)).local_table.name
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
        "canvas_beefapi_connections",
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
    project = next(
        (x for x in session.new if isinstance(x, Project) and x.id == entity.project_id), None
    )
    if project is None:
        project = session.scalar(select(Project).where(Project.id == entity.project_id))
    if project is None or project.workspace_mode != "infinite_canvas":
        raise NotFound("Infinite canvas project does not exist")
    canvas_id = getattr(entity, "canvas_id", None) or getattr(entity, "primary_canvas_id", None)
    if canvas_id:
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
    from short_drama.service.base import utcnow

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
    for entity in changed:
        table = inspect(type(entity)).local_table
        name = table.name
        if name in {
            "users",
            "user_sessions",
            "email_challenges",
            "email_outbox",
            "auth_rate_limits",
            "project_invitations",
            "project_members",
            "audit_events",
            "user_model_preferences",
            "user_project_states",
        }:
            continue
        new = entity in session.new
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
            _guard_canvas_write(session, entity, actor, new)
        if new:
            if name in {"projects", "ai_model_configs"}:
                entity.owner_user_id = user_id
            elif name == "global_assets":
                entity.user_id = user_id
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
            "episode_render_jobs",
            "ai_generation_records",
            "media_assets",
            "novel_script_records",
            "script_shot_records",
            "asset_image_candidates",
            "episode_scripts",
            "media_files",
            "agent_artifacts",
            "canvas_binary_resources",
        }:
            # Session.get may return an object already in the identity map. Verify
            # the stored ownership before accepting writes to private resources.
            if session.scalar(select(type(entity).id).where(type(entity).id == entity.id)) is None:
                raise NotFound("Resource does not exist")
        if name in {"episode_scripts", "media_files", "canvas_binary_resources"} and not new:
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
            if not disposal:
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
        for field, target_name in [
            ("media_id", "media_files"),
            ("first_frame_media_id", "media_files"),
            ("last_frame_media_id", "media_files"),
            ("output_media_id", "media_files"),
            ("current_media_id", "media_files"),
            ("proxy_media_id", "media_files"),
            ("asset_id", "assets"),
            ("binary_id", "canvas_binary_resources"),
        ]:
            if field not in table.c or not getattr(entity, field, None):
                continue
            model = next(
                m.class_ for m in Base.registry.mappers if m.local_table.name == target_name
            )
            identifier = int(getattr(entity, field))
            target = next(
                (x for x in session.new if isinstance(x, model) and x.id == identifier), None
            )
            if target is None:
                target = session.scalar(select(model).where(model.id == identifier))
            allowed_scopes = {entity_scope}
            if name == "agent_attachments" and field == "media_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_user_media_references" and field == "media_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_user_binary_references" and field == "binary_id":
                allowed_scopes.add((actor.user_id, None))
            if name == "canvas_library_asset_references":
                allowed_scopes.add((actor.user_id, None))
                if target is not None and scope_of(session, target) not in allowed_scopes:
                    from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
                    from short_drama.service.canvas_document import media_references

                    parent = _canvas_library_parent(session, entity.library_asset_id)
                    original_ids = {value for _, value in media_references(parent.payload_json)}
                    if entity in session.deleted:
                        for previous in inspect(parent).attrs.payload_json.history.deleted:
                            original_ids.update(value for _, value in media_references(previous))
                    ancestors = CanvasResourceDAO(session).copy_ancestors({identifier})
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
                target = session.scalar(select(MediaFile).where(MediaFile.id == int(identifier)))
                if target is None or scope_of(session, target) != entity_scope:
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
        }:
            continue
        action = "create" if new else "delete" if entity in session.deleted else "update"
        if (
            name in {"episode_scripts", "media_files", "canvas_binary_resources"}
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
