from sqlalchemy import inspect, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.scope.resource_scope import (
    _canvas_library_parent,
    _canvas_upload_parent,
)
from short_drama.db.scope.resource_scope import (
    require_project as require_project,
)
from short_drama.db.scope.resource_scope import (
    scope_of as scope_of,
)
from short_drama.domain import (
    CANVAS_PRIVATE_TABLES,
    AIModelConfig,
)


def _canvas_snapshot_reads(session, actor):
    from short_drama.domain import (
        CanvasBinaryResource,
        CanvasUserBinaryReference,
        MediaFile,
        Project,
        ProjectCanvas,
    )
    from short_drama.domain.canvas.document.canvas import CanvasUserMediaReference

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
        from short_drama.db.access.policies.canvas_style_access import guard_canvas_style

        guard_canvas_style(session, entity, actor, new)
        return
    if name == "canvas_local_jobs":
        from short_drama.db.access.policies.canvas_local_access import guard_canvas_local

        guard_canvas_local(session, entity, actor, new)
        return
    if name in {"canvas_render_jobs", "canvas_render_media_references"}:
        from short_drama.db.access.policies.canvas_render_access import guard_canvas_render

        guard_canvas_render(session, entity, actor, new)
        return
    if name in {"canvas_tool_runs", "canvas_tool_submissions"}:
        from short_drama.db.access.policies.canvas_tool_run_access import guard_canvas_tool_run

        guard_canvas_tool_run(session, entity, actor, new)
        return
    if name in {"canvas_task_bindings", "canvas_task_media_references", "canvas_results"}:
        from short_drama.db.access.policies.canvas_generation_access import guard_canvas_generation

        guard_canvas_generation(session, entity, actor, new)
        return
    if name in {"canvas_project_folders", "canvas_project_folder_items"}:
        from short_drama.db.access.policies.canvas_folder_access import guard_canvas_folder

        guard_canvas_folder(session, entity, actor, new)
        return
    if name in {"canvas_creation_attempts", "canvas_creation_resources"}:
        from short_drama.db.access.policies.canvas_creation_access import guard_canvas_creation

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
