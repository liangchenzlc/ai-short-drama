from functools import lru_cache

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Mapper, with_loader_criteria
from sqlalchemy.orm.interfaces import ORMOption

from short_drama.db.access.policies.canvas_recycle_access import CanvasRecycleScope
from short_drama.db.scope.resource_scope import (
    require_project as require_project,
)
from short_drama.db.scope.resource_scope import (
    scope_of as scope_of,
)
from short_drama.domain import (
    CANVAS_PRIVATE_TABLES,
    Base,
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
        "agent_task_requirements",
        "agent_task_controls",
        "agent_operations",
        "agent_media_requests",
        "agent_graph_checkpoints",
        "agent_graph_writes",
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
    if name in {"script_shot_records"}:
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
