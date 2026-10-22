"""Frozen candidate source identity and the current pending plan lookup."""

from sqlalchemy import select


def source_snapshot(step, run, now):
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


def _pending_parent(session, run):
    from short_drama.domain.agent import AgentRun

    # Earlier queued supplements may still point at a plan replaced by a prior
    # supplement. Always follow the current pending plan in this conversation.
    query = select(AgentRun).where(
        AgentRun.conversation_id == run.conversation_id,
        AgentRun.id < run.id,
        AgentRun.status == "waiting_review",
    )
    parents = session.scalars(query.order_by(AgentRun.id.desc()).with_for_update()).all()
    return next(
        (parent for parent in parents if not (parent.checkpoint or {}).get("awaiting_artifacts")),
        None,
    )
