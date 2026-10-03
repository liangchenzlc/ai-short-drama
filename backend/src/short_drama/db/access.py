"""Scoped ORM queries plus write/reference checks. API sessions always carry an actor.

Subqueries use Core aliases intentionally: applying ORM criteria inside their own
authorization predicate would recurse. Unscoped sessions are reserved for workers,
authentication and migration tools, never accepted from an HTTP payload.
"""

from sqlalchemy import and_, event, false, inspect, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import AGENT_PRIVATE_TABLES, AGENT_TABLES, Base
from short_drama.domain.collaboration import AuditEvent, ResourceScope
from short_drama.utils.snowflake import next_id


def project_ids(user_id):
    projects = Base.metadata.tables["projects"].alias("allowed_projects")
    members = Base.metadata.tables["project_members"].alias("allowed_members")
    return select(projects.c.id).where(
        projects.c.archived_at.is_(None),
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
    # Private execution data never inherits the ordinary shared-project predicate.
    if name == "agent_conversations":
        return and_(c.owner_user_id == user_id, c.project_id.in_(project_ids(user_id)))
    if name in {"agent_messages", "agent_runs", "agent_events"}:
        return c.conversation_id.in_(allowed_ids("agent_conversations", user_id))
    if name in {"agent_turns", "agent_tool_calls"}:
        return c.run_id.in_(allowed_ids("agent_runs", user_id))
    if name == "agent_artifacts":
        return c.project_id.in_(project_ids(user_id))
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
            c.project_id.in_(project_ids(user_id)),
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
        }
        if any(
            getattr(column, "key", column) in protected for column in state.statement._values or {}
        ):
            raise WorkflowError(
                "guarded_bulk_write", "Ownership and references require checked writes", 403
            )
    if state.is_select or state.is_update or state.is_delete:
        statement = state.statement
        for mapper in Base.registry.mappers:
            statement = statement.options(
                with_loader_criteria(
                    mapper.class_,
                    condition(mapper.local_table, actor.user_id),
                    include_aliases=True,
                )
            )
        state.statement = statement


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
        member = session.scalar(
            select(ProjectMember)
            .where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == actor.user_id,
                ProjectMember.status == "active",
            )
            .with_for_update()
        )
        if not member:
            raise NotFound("Project does not exist")
        if owner:
            raise WorkflowError("project_owner_required", "Only the project owner can do this", 403)


def scope_of(session, entity):
    """Resolve the actual ownership path, independent of the caller's entry point."""
    table = inspect(type(entity)).local_table
    name = table.name
    if name in AGENT_PRIVATE_TABLES:
        conversation = _agent_conversation(session, entity)
        return (None, conversation.project_id)
    if name == "projects":
        return (None, entity.id)
    if isinstance(entity, ResourceScope):
        return (entity.scope_user_id, entity.project_id)
    if name == "ai_model_configs":
        return (entity.owner_user_id, None)
    if name == "global_assets":
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
        if actor and name in AGENT_TABLES:
            _guard_agent_write(session, entity, actor, new)
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
        entity_scope = scope_of(session, entity)
        if entity_scope[1] and not (new and name == "projects"):
            require_project(session, entity_scope[1])
        elif not entity_scope[1] and entity_scope[0] != actor.user_id:
            raise NotFound("Resource does not exist")
        if name == "projects" and entity in session.deleted:
            require_project(session, entity.id, owner=True)
        # Ownership is immutable through normal edits.
        for field in ("owner_user_id", "scope_user_id", "project_id", "initiated_by"):
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
            if target is None or scope_of(session, target) != entity_scope:
                raise NotFound("Reference is outside this resource scope")
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
        if name in AGENT_PRIVATE_TABLES:
            continue
        session.info.setdefault("pending_audit", []).append(
            AuditEvent(
                id=next_id(),
                actor_user_id=actor.user_id,
                project_id=entity_scope[1],
                object_type=name,
                object_id=str(getattr(entity, "id", getattr(entity, "project_id", ""))),
                action="create" if new else "delete" if entity in session.deleted else "update",
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
