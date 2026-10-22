"""Ownership parent resolution shared by scoped reads and immutable-write guards."""

from sqlalchemy import inspect, select

from short_drama.core.exceptions import NotFound
from short_drama.domain import AGENT_PRIVATE_TABLES, Base
from short_drama.domain.collaboration import ResourceScope


def require_project(session, project_id, *, owner=False, lock=True):
    from short_drama.domain import Project

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
        raise NotFound("Project does not exist")


def scope_of(session, entity):
    """Resolve the actual ownership path, independent of the caller's entry point."""
    table = inspect(type(entity)).local_table
    if table.name == "image_group_items":
        from short_drama.domain import ImageGroup

        parent = session.get(ImageGroup, entity.group_id)
        if parent is None:
            raise NotFound("图片组不存在")
        return scope_of(session, parent)
    if table.name == "canvas_style_media_references":
        from short_drama.domain import CanvasStyleProfile

        parent = session.get(CanvasStyleProfile, entity.style_id)
        if parent is None:
            raise NotFound("画风原件不存在或无权访问")
        return scope_of(session, parent)
    if table.name == "canvas_local_jobs":
        return (None, entity.project_id) if entity.project_id else (entity.user_id, None)
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
    if name in {
        "agent_skills",
        "agent_preferences",
        "agent_skill_settings",
        "agent_skill_versions",
    }:
        return (entity.owner_user_id, None)
    if name in {"agent_memory_items", "agent_memory_jobs", "agent_conversation_summaries"}:
        return (entity.owner_user_id, None)
    if name in {"agent_memory_revisions", "agent_memory_sources", "agent_memory_forget_rules"}:
        return (_agent_parent(session, "agent_memory_items", entity.memory_id).owner_user_id, None)
    if name == "agent_attachments":
        conversation = _agent_parent(session, "agent_conversations", entity.conversation_id)
        return (
            (conversation.owner_user_id, None)
            if conversation.scope_version == 3
            else (None, conversation.project_id)
        )
    if name in AGENT_PRIVATE_TABLES:
        conversation = _agent_conversation(session, entity)
        return (
            (conversation.owner_user_id, None)
            if conversation.scope_version == 3
            else (None, conversation.project_id)
        )
    if name == "projects":
        return (None, entity.id)
    if name == "canvas_style_profiles":
        return (entity.scope_user_id, None)
    if isinstance(entity, ResourceScope):
        return (entity.scope_user_id, entity.project_id)
    if name == "ai_model_configs":
        return (entity.owner_user_id, None)
    if name in {
        "canvas_style_preferences",
        "canvas_workspace_user_states",
        "canvas_model_catalogs",
        "canvas_channel_models",
    }:
        return (entity.user_id, None)
    if "project_id" in table.c and entity.project_id:
        return (None, entity.project_id)
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
    if name in {
        "agent_messages",
        "agent_runs",
        "agent_events",
        "agent_tasks",
        "agent_discussion_revisions",
        "agent_batch_undos",
    }:
        return _agent_parent(session, "agent_conversations", entity.conversation_id)
    if name in {
        "agent_task_inputs",
        "agent_task_controls",
        "agent_operations",
        "agent_media_requests",
    }:
        task = _agent_parent(session, "agent_tasks", entity.task_id)
        return _agent_parent(session, "agent_conversations", task.conversation_id)
    if name == "agent_turns" or name == "agent_tool_calls":
        run = _agent_parent(session, "agent_runs", entity.run_id)
        return _agent_parent(session, "agent_conversations", run.conversation_id)
    raise NotFound("Agent parent does not exist")


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
