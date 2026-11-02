"""Atomic admission and result collection for existing text/image/video tasks."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.core.access import lock_run, may_decide
from short_drama.agent.core.events import TERMINAL, append_event
from short_drama.agent.core.state import (
    append_late_artifact_message,
    finish_locked,
)
from short_drama.agent.session.lifecycle import finalize_deletion_locked
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    MediaAsset,
)
from short_drama.domain.assistant.artifacts.models import AgentArtifact
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id

NATIVE_KINDS = {"extract", "storyboard", "image", "video"}


def collect_native_results(factory, settings, limit=100):
    """Waiting costs no worker slot. Accepted results may archive after cancellation."""
    with factory() as session:
        ids = session.scalars(
            select(AgentToolCall.id)
            .join(AsyncTask, AsyncTask.id == AgentToolCall.generation_task_id)
            .where(
                AsyncTask.status.in_(TERMINAL),
                AgentToolCall.status.in_(("waiting_generation", "cancelled")),
                AgentToolCall.result["settled"].as_boolean().is_(False),
            )
            .order_by(AgentToolCall.id)
            .limit(limit)
        ).all()
    collected = 0
    for identifier in ids:
        with factory.begin() as session:
            run_id = session.scalar(
                select(AgentToolCall.run_id).where(AgentToolCall.id == identifier)
            )
            rows = lock_run(session, run_id, skip_locked=True)
            if rows is None:
                continue
            project, conversation, run = rows
            tool = session.scalar(
                select(AgentToolCall)
                .where(AgentToolCall.id == identifier)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            result = deepcopy(tool.result or {})
            if result.get("settled") is not False:
                continue
            task = session.scalar(
                select(AsyncTask).where(AsyncTask.id == tool.generation_task_id).with_for_update()
            )
            if task.status not in TERMINAL:
                continue
            record = session.scalar(
                select(AIGenerationRecord)
                .where(AIGenerationRecord.task_id == task.id)
                .order_by(AIGenerationRecord.call_no.desc())
                .limit(1)
                .with_for_update()
            )
            step = next(
                item
                for item in run.checkpoint["authorization"]["steps"]
                if item["id"] == result["step_id"]
            )
            artifacts = []
            media = session.scalars(
                select(MediaAsset)
                .where(MediaAsset.record_id == record.id)
                .order_by(MediaAsset.output_index)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            native_text_ready = task.status == "succeeded" and result["kind"] in {
                "extract",
                "storyboard",
            }
            entries = media if media else [None] if native_text_ready else []
            for index, item in enumerate(entries, 1):
                existing = session.scalar(
                    select(AgentArtifact)
                    .where(
                        AgentArtifact.tool_call_id == tool.id, AgentArtifact.result_index == index
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if existing:
                    artifacts.append(str(existing.id))
                    continue
                now = utcnow()
                kind = {
                    "extract": "extraction_candidate",
                    "storyboard": "storyboard_candidate",
                    "image": "image_candidate",
                    "video": "video_candidate",
                }[result["kind"]]
                from short_drama.agent.native.sources import source_snapshot

                source = source_snapshot(step, run, now)
                source["model_name"] = record.config_snapshot["name"]
                artifact = AgentArtifact(
                    id=next_id(),
                    project_id=conversation.project_id,
                    tool_call_id=tool.id,
                    result_index=index,
                    kind=kind,
                    status="ready",
                    row_version=1,
                    source_snapshot=source,
                    source_content=None,
                    metadata_json={
                        "label": {
                            "extract": "资产提取候选",
                            "storyboard": "分镜候选",
                            "image": "图片候选",
                            "video": "视频候选",
                        }[result["kind"]],
                        "native_parameters": deepcopy(
                            record.request_data.get("resolved_parameters", {})
                        ),
                    },
                    generation_task_id=task.id,
                    media_asset_id=item.id if item else None,
                    media_id=item.media_id if item else None,
                    target_asset_id=int(step["target_id"])
                    if step["target_kind"] == "asset"
                    else None,
                    target_shot_id=int(step["target_id"])
                    if step["target_kind"] == "shot"
                    else None,
                    created_by=run.initiated_by,
                    created_at=now,
                    updated_at=now,
                )
                session.add(artifact)
                artifacts.append(str(artifact.id))
            active = (
                run.status not in TERMINAL
                and not run.cancel_requested
                and settings.agent_enabled
                and may_decide(session, project, conversation, run)
            )
            result.update(
                settled=True,
                status=task.status,
                artifact_ids=artifacts,
                error={"code": (task.error or {}).get("code", "generation_failed")}
                if task.status != "succeeded"
                else None,
            )
            tool.result, tool.updated_at = result, utcnow()
            if tool.status != "cancelled":
                tool.status = "succeeded" if task.status == "succeeded" else "failed"
            if active:
                append_event(
                    session,
                    conversation,
                    "artifacts.created",
                    {"run_id": str(run.id), "artifact_ids": artifacts},
                    run_id=run.id,
                )
            else:
                if run.status not in TERMINAL:
                    finish_locked(
                        session, conversation, run, "cancelled", {"code": "access_revoked"}
                    )
                if artifacts:
                    session.flush()
                    append_late_artifact_message(session, conversation, run, set(artifacts))
            session.flush()
            finalize_deletion_locked(session, conversation)
            collected += 1
    return collected
