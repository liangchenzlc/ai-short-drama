from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.time import utcnow
from short_drama.db.access.agent_writes import _guard_agent_write
from short_drama.db.access.canvas_writes import _canvas_snapshot_reads, _guard_canvas_write
from short_drama.db.access.policies.canvas_recycle_access import recycle_scope
from short_drama.db.scope.canvas_resource_scope import copy_ancestors
from short_drama.db.scope.resource_scope import (
    _agent_parent,
    _canvas_library_parent,
)
from short_drama.db.scope.resource_scope import (
    require_project as require_project,
)
from short_drama.db.scope.resource_scope import (
    scope_of as scope_of,
)
from short_drama.domain import (
    AGENT_PRIVATE_TABLES,
    AGENT_TABLES,
    CANVAS_PRIVATE_TABLES,
    CANVAS_TABLES,
    Base,
)
from short_drama.domain.identity.audit.models import AuditEvent
from short_drama.domain.media.imports.scope import ResourceScope
from short_drama.utils.snowflake import next_id


@event.listens_for(Session, "before_flush")
def guard_writes(session, _context, _instances):
    from short_drama.db.access.policies.agent_skill_access import guard_skill_versions
    from short_drama.db.access.policies.asset_version_access import guard_version_writes

    guard_version_writes(session)
    guard_skill_versions(session)
    actor = session.info.get("actor")
    if not actor:
        return
    user_id = actor.user_id
    scope = session.info.get("resource_scope", (user_id, session.info.get("request_project")))
    if scope[1]:
        scope = (None, scope[1])
    changed = (
        list(session.new)
        + [x for x in session.dirty if session.is_modified(x, include_collections=False)]
        + list(session.deleted)
    )
    snapshot_references, snapshot_reads = _canvas_snapshot_reads(session, actor)
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
        }:
            continue
        new = entity in session.new
        if name == "user_creation_drafts":
            if entity.user_id != actor.user_id:
                raise NotFound("私人创作草稿不存在")
            if not new and any(
                inspect(entity).attrs[field].history.has_changes()
                for field in ("user_id", "project_id", "object_id", "purpose")
            ):
                raise WorkflowError("ownership_immutable", "私人草稿所属和用途不可修改", 403)
        if name in {"agent_skills", "agent_attachments"}:
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
        if name in AGENT_TABLES:
            _guard_agent_write(session, entity, actor, new)
        if name in CANVAS_TABLES:
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
        if not new and name in {
            "async_tasks",
            "generation_batches",
            "generation_batch_items",
            "project_render_jobs",
            "ai_generation_records",
            "media_assets",
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
