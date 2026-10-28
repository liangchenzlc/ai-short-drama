from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Asset, Project, ShotScript
from short_drama.utils.json_digest import digest as digest


def check_source(session, step):
    source = step["source"]
    creation = session.scalar(
        select(Project)
        .where(Project.id == int(source["project_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if creation is None or creation.content_version != source["content_version"]:
        raise WorkflowError("agent_source_changed", "正文已变更，请重新核对任务范围", 409)
    if (
        step["kind"] in {"storyboard", "shot_patch", "image", "video"}
        and creation.storyboard_version != source["storyboard_version"]
    ):
        raise WorkflowError("agent_source_changed", "分镜已变更，请重新核对任务范围", 409)
    if step["target_kind"] != "project":
        model = Asset if step["target_kind"] == "asset" else ShotScript
        target = session.scalar(
            select(model)
            .where(model.id == int(step["target_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            target is None
            or target.row_version != source["target_row_version"]
            or getattr(target, "deleted_at", None)
        ):
            raise WorkflowError("agent_source_changed", "任务对象已变更，请重新核对", 409)


def source_snapshot(step, run, now):
    """Build a frozen source record for a candidate creation step."""
    source = step["source"]
    return {
        "project_id": str(source["project_id"]),
        "content_version": source["content_version"],
        "storyboard_version": source["storyboard_version"],
        "project_row_version": source["project_row_version"],
        "target_kind": step["target_kind"],
        "target_id": str(step["target_id"]),
        "target_row_version": source.get("target_row_version"),
        "model_name": step.get("model_name") or run.config_snapshot.get("name"),
        "generated_at": now.isoformat(),
    }
