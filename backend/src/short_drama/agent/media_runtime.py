"""Settle private assistant media batches after native generation and protected adoption."""

import logging
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from short_drama.agent.access import lock_run
from short_drama.agent.media_tools import batch_receipt_locked
from short_drama.agent.run_events import TERMINAL
from short_drama.agent.state import mark_scheduled
from short_drama.agent.task_queries import task_for_run
from short_drama.agent.task_state import task_event
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AsyncTask,
    ImageGroup,
    ImageGroupItem,
    MediaAsset,
    Project,
    ShotScript,
)
from short_drama.domain.agent import AgentToolCall
from short_drama.domain.agent_media import AgentMediaRequest
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.schemas.assistant_media import MEDIA_USAGE_KEYS
from short_drama.service.assistant_canvas_media_service import (
    canvas_media_changes_locked,
    settle_canvas_media_locked,
)
from short_drama.service.assistant_dialogue_service import (
    dialogue_audio_changes_locked,
    settle_dialogue_audio_locked,
)
from short_drama.service.assistant_image_service import image_adoption_state
from short_drama.service.assistant_shot_video_service import (
    settle_shot_video_locked,
    shot_video_changes_locked,
)
from short_drama.service.assistant_voice_service import (
    settle_voice_design_locked,
    voice_design_changes_locked,
)
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest

logger = logging.getLogger(__name__)
SETTLED = frozenset({"succeeded", "failed", "conflict", "not_submitted"})


def _reservation(task, intent, state):
    if intent.reservation_state != "reserved":
        return
    usage = deepcopy(task.media_usage)
    meter = usage[MEDIA_USAGE_KEYS[intent.media_kind]]
    meter["reserved"] -= intent.requested_count
    if state == "committed":
        meter["submitted"] += intent.requested_count
    intent.reservation_state = state
    task.media_usage = usage


def _settle_media(session, owner, task, intent, settings):
    native = session.scalar(
        select(AsyncTask)
        .where(
            AsyncTask.id == intent.generation_task_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    record = session.scalar(
        select(AIGenerationRecord)
        .where(
            AIGenerationRecord.task_id == intent.generation_task_id,
        )
        .order_by(AIGenerationRecord.call_no.desc())
        .limit(1)
        .with_for_update()
    )
    if native is None or record is None:
        raise RuntimeError("Assistant media intent lost its native task or record")
    if native.status not in {"succeeded", "failed", "cancelled"}:
        return
    if record.status in {"sent", "unknown"}:
        intent.status = "unknown"
        intent.error = {
            "code": "assistant_media_acceptance_unknown",
            "message": "原生成请求待核实，已保留原身份与数量预占，请勿重新提交",
        }
        return
    if record.status == "prepared" and record.started_at is None:
        _reservation(task, intent, "released")
        intent.status = "not_submitted"
        intent.error = {
            "code": (native.error or {}).get("code", "generation_not_submitted"),
            "message": "本项尚未外发，已释放数量预占",
        }
        return
    _reservation(task, intent, "committed")
    if intent.frozen_target["kind"] == "node":
        outputs = {
            output.output_index: str(output.media_id)
            for output in session.scalars(
                select(MediaAsset).where(MediaAsset.record_id == record.id)
            )
        }
        intent.result, intent.status, intent.error = settle_canvas_media_locked(
            session, owner, intent, native, outputs
        )
        usage = deepcopy(task.media_usage)
        usage[MEDIA_USAGE_KEYS[intent.media_kind]]["succeeded"] += len(intent.result["items"])
        task.media_usage = usage
        intent.updated_at = utcnow()
        return
    if intent.frozen_target.get("media_role") == "character_voice":
        outputs = list(
            session.scalars(
                select(MediaAsset)
                .where(MediaAsset.record_id == record.id)
                .order_by(MediaAsset.output_index)
            )
        )
        intent.result, intent.status, intent.error = settle_voice_design_locked(
            session, owner, intent, native, record, outputs, settings
        )
        usage = deepcopy(task.media_usage)
        usage["audio"]["succeeded"] += len(outputs)
        task.media_usage = usage
        intent.updated_at = utcnow()
        return
    if intent.frozen_target.get("media_role") == "dialogue_audio":
        outputs = list(
            session.scalars(
                select(MediaAsset)
                .where(MediaAsset.record_id == record.id)
                .order_by(MediaAsset.output_index)
            )
        )
        intent.result, intent.status, intent.error = settle_dialogue_audio_locked(
            session, owner, intent, native, outputs, settings
        )
        usage = deepcopy(task.media_usage)
        usage["audio"]["succeeded"] += len(outputs)
        task.media_usage = usage
        intent.updated_at = utcnow()
        return
    if intent.media_kind == "video":
        outputs = list(
            session.scalars(
                select(MediaAsset)
                .where(MediaAsset.record_id == record.id)
                .order_by(MediaAsset.output_index)
            )
        )
        intent.result, intent.status, intent.error = settle_shot_video_locked(
            session, owner, intent, native, outputs, settings=settings
        )
        usage = deepcopy(task.media_usage)
        usage["videos"]["succeeded"] += len(outputs)
        task.media_usage = usage
        intent.updated_at = utcnow()
        return
    group = session.scalar(
        select(ImageGroup).where(ImageGroup.task_id == native.id).with_for_update()
    )
    if group is None:
        raise RuntimeError("Assistant image task lost its image group")
    items = session.scalars(
        select(ImageGroupItem)
        .where(
            ImageGroupItem.group_id == group.id,
        )
        .order_by(ImageGroupItem.output_index)
        .with_for_update()
    ).all()
    primary = next((item for item in items if item.output_index == group.primary_index), None)
    outputs = {
        output.output_index: str(output.media_id)
        for output in session.scalars(select(MediaAsset).where(MediaAsset.record_id == record.id))
    }
    adopted = (
        primary is not None
        and group.primary_mode == "auto"
        and str(primary.media_id) == outputs.get(primary.output_index)
    )
    ready = [item for item in items if item.output_index in outputs]
    target = intent.frozen_target
    row = session.get(Asset if target["kind"] == "asset" else ShotScript, int(target["id"]))
    intent.result = {
        "image_group_id": str(group.id),
        "group_status": group.status,
        "adopted": adopted,
        "primary_index": group.primary_index,
        "primary_version": str(group.primary_version),
        "target_version": str(int(target["row_version"]) + 1) if adopted else target["row_version"],
        "current_target_version": str(row.row_version) if row else None,
        **(
            {
                "storyboard_transition": deepcopy(
                    group.source_context["assistant_storyboard_transition"]
                )
            }
            if adopted and group.source_context.get("assistant_storyboard_transition")
            else {}
        ),
        "items": [
            {
                "id": str(item.id),
                "output_index": item.output_index,
                "item_version": str(item.item_version),
                "media_id": outputs.get(item.output_index),
                "status": "ready" if item.output_index in outputs else "failed",
            }
            for item in items
        ],
    }
    intent.result = {
        **intent.result,
        "catalog": {
            "target_kind": target["kind"],
            "target_id": target["id"],
            "target_version": intent.result["target_version"],
            "groups": [
                {
                    "id": str(group.id),
                    "kind": group.kind,
                    "status": group.status,
                    "is_current": group.active_slot == 1,
                    "expected_count": group.expected_count,
                    "primary_index": group.primary_index if adopted else None,
                    "primary_version": str(group.primary_version),
                    "created_at": group.created_at.isoformat(),
                    "items": [{**item, "history_media_ids": []} for item in intent.result["items"]],
                }
            ],
        },
    }
    usage = deepcopy(task.media_usage)
    usage["images"]["succeeded"] += len(ready)
    task.media_usage = usage
    if ready and not adopted:
        intent.status = "conflict"
        intent.error = {
            "code": "assistant_media_adoption_conflict",
            "message": group.notice or "来源或主图已变化，成功产物已保留在图片组中",
        }
    elif native.status != "succeeded" or len(ready) != intent.requested_count:
        intent.status = "failed"
        intent.error = {
            "code": "assistant_media_partial_failure",
            "message": "本项未全部生成成功，成功产物及历史已保留，不自动补发",
        }
    else:
        intent.status, intent.error = "succeeded", None
    intent.updated_at = utcnow()


def _result_refs(intents):
    standard = [
        {
            "kind": item.frozen_target["kind"],
            "id": item.frozen_target["id"],
            "project_id": item.frozen_target["project_id"],
            "name": f"图片组 {item.result['image_group_id']}",
            "status": "adopted"
            if item.result["adopted"]
            else "adoption_conflict"
            if item.status == "conflict"
            else "generation_failed",
            "row_version": item.result["target_version"],
            "image_group_id": item.result["image_group_id"],
            "primary_version": item.result["primary_version"],
            "generation_id": str(item.generation_task_id),
            "generated_count": sum(entry["status"] == "ready" for entry in item.result["items"]),
            "requested_count": item.requested_count,
        }
        for item in intents
        if item.result and item.result.get("image_group_id")
    ]
    return (
        standard
        + [
            {
                "kind": "sound_line",
                "id": item.frozen_target["id"],
                "project_id": item.frozen_target["project_id"],
                "name": item.frozen_target["name"],
                "media_kind": "audio",
                "media_role": "dialogue_audio",
                "status": "source_unavailable"
                if item.result.get("target_available") is False
                else "adopted"
                if item.result["adopted"]
                else "adoption_conflict"
                if item.status == "conflict"
                else "generation_failed",
                "row_version": item.result["target_version"],
                "media_id": item.result["items"][0]["media_id"] if item.result["items"] else None,
                "generation_id": str(item.generation_task_id),
                "generated_count": len(item.result["items"]),
                "requested_count": item.requested_count,
            }
            for item in intents
            if item.frozen_target.get("media_role") == "dialogue_audio" and item.result
        ]
        + [
            {
                "kind": "asset",
                "id": item.frozen_target["id"],
                "project_id": item.frozen_target["project_id"],
                "name": item.frozen_target["name"],
                "media_kind": "audio",
                "media_role": "character_voice",
                "status": "source_unavailable"
                if item.result.get("target_available") is False
                else "adopted"
                if item.result["adopted"]
                else "adoption_conflict"
                if item.status == "conflict"
                else "generation_failed",
                "row_version": item.result["target_version"],
                "voice_version": item.result["voice_version"],
                "media_id": item.result["items"][0]["media_id"] if item.result["items"] else None,
                "generation_id": str(item.generation_task_id),
                "generated_count": len(item.result["items"]),
                "requested_count": item.requested_count,
            }
            for item in intents
            if item.frozen_target.get("media_role") == "character_voice" and item.result
        ]
        + [
            {
                "kind": "shot",
                "id": item.frozen_target["id"],
                "project_id": item.frozen_target["project_id"],
                "name": item.frozen_target["name"],
                "media_kind": "video",
                "status": "source_unavailable"
                if item.result.get("target_available") is False
                else "adopted"
                if item.result["adopted"]
                else "adoption_conflict"
                if item.status == "conflict"
                else "generation_failed",
                "row_version": item.result["target_version"],
                "generation_id": str(item.generation_task_id),
                "generated_count": len(item.result["items"]),
                "requested_count": item.requested_count,
            }
            for item in intents
            if item.media_kind == "video" and item.frozen_target["kind"] == "shot" and item.result
        ]
        + [
            {
                "kind": "canvas_node",
                "id": item.frozen_target["id"],
                "project_id": item.frozen_target["project_id"],
                "canvas_id": item.frozen_target["canvas_id"],
                "name": item.frozen_target["name"],
                "media_kind": item.media_kind,
                "status": "source_unavailable"
                if item.result.get("target_available") is False
                else "bound"
                if item.result["bound"]
                else "adoption_conflict"
                if item.status == "conflict"
                else "generation_failed",
                "row_version": item.result["target_version"],
                "private_revision": item.result["private_revision"],
                "generation_id": str(item.generation_task_id),
                "generated_count": len(item.result["items"]),
                "requested_count": item.requested_count,
            }
            for item in intents
            if item.frozen_target["kind"] == "node" and item.result
        ]
    )


def settle_batch_locked(session, conversation, task, run, operation, settings):
    intents = session.scalars(
        select(AgentMediaRequest)
        .where(
            AgentMediaRequest.operation_id == operation.id,
        )
        .order_by(AgentMediaRequest.item_index)
        .with_for_update()
    ).all()
    for intent in intents:
        if intent.status not in SETTLED:
            _settle_media(session, conversation.owner_user_id, task, intent, settings)
    result = batch_receipt_locked(session, task, operation)
    shot_adoptions = [
        item.result
        for item in intents
        if item.frozen_target["kind"] == "shot" and (item.result or {}).get("adopted")
    ]
    if shot_adoptions and all(item.get("storyboard_transition") for item in shot_adoptions):
        transitions = sorted(
            (item["storyboard_transition"] for item in shot_adoptions),
            key=lambda item: int(item["before"]),
        )
        if all(
            left["after"] == right["before"]
            for left, right in zip(transitions, transitions[1:], strict=False)
        ):
            result["before_storyboard_version"] = transitions[0]["before"]
            result["storyboard_version"] = transitions[-1]["after"]
    if intents:
        result["project_id"] = intents[0].frozen_target["project_id"]
        if intents[0].frozen_target["kind"] == "node":
            result["canvas_id"] = intents[0].frozen_target["canvas_id"]
            result["before_canvas_revision"] = intents[0].frozen_target.get("canvas_revision")
            result["canvas_revision"] = str(
                max(int((item.result or {}).get("canvas_revision", 0)) for item in intents)
            )
    refs = _result_refs(intents)
    changed = digest(operation.result) != digest(result) or operation.result_refs != refs
    operation.result, operation.result_refs = result, refs
    if not result["settled"]:
        phase = (
            "reconciling" if any(item.status == "unknown" for item in intents) else "waiting_media"
        )
        if operation.phase != phase or changed:
            operation.status, operation.phase = phase, phase
            operation.row_version += 1
            task.phase, task.row_version = phase, task.row_version + 1
            task_event(
                session,
                conversation,
                task,
                f"step.{phase}",
                run=run,
                call_id=str(operation.tool_call_id),
                operation_id=str(operation.id),
                tool_name=operation.tool_name,
                phase=phase,
                trace_version=str(operation.row_version),
                result_refs=refs,
            )
        return False
    operation.changes = []
    changes = []
    for intent in intents:
        if intent.frozen_target["kind"] == "node":
            changes.extend(canvas_media_changes_locked(session, conversation.owner_user_id, intent))
            continue
        if intent.frozen_target.get("media_role") == "character_voice":
            changes.extend(voice_design_changes_locked(session, intent))
            continue
        if intent.frozen_target.get("media_role") == "dialogue_audio":
            changes.extend(dialogue_audio_changes_locked(session, intent))
            continue
        if intent.media_kind == "video":
            changes.extend(shot_video_changes_locked(session, intent))
            continue
        if intent.result and intent.result.get("adopted"):
            target = intent.frozen_target
            group = session.get(ImageGroup, int(intent.result["image_group_id"]))
            snapshot = (group.source_context or {}).get("assistant_adoption_snapshot")
            row = session.get(Asset if target["kind"] == "asset" else ShotScript, int(target["id"]))
            if (
                snapshot
                and "image_groups" in target["before"]
                and row is not None
                and image_adoption_state(session, row, target["kind"]) == snapshot
            ):
                changes.append(
                    {
                        "kind": target["kind"],
                        "id": target["id"],
                        "media_role": "image",
                        "before": target["before"],
                        "after": deepcopy(snapshot),
                    }
                )
    operation.changes = changes
    failed = [item for item in intents if item.status != "succeeded"]
    operation.status = (
        "conflict"
        if any(item.status == "conflict" for item in intents)
        else "failed"
        if failed
        else "succeeded"
    )
    if failed:
        result["error"] = {
            "code": "assistant_media_batch_failed",
            "message": "媒体批次未全部完成，成功产物已保留，请核对回执后明确继续",
        }
    task.status = "running"
    call = session.get(AgentToolCall, operation.tool_call_id)
    call.status = "succeeded" if operation.status == "succeeded" else "failed"
    from short_drama.agent.assistant_tools import finish_operation_locked

    finish_operation_locked(session, conversation, task, run, operation, call, result)
    if run.status not in TERMINAL:
        mark_scheduled(run, phase="tools")
    return True


def collect_media_results(factory, settings, limit=100):
    # No model calls or busy polling; the existing scheduler revisits durable waits.
    with factory() as session:
        ids = session.scalars(
            select(AgentOperation.id)
            .where(
                AgentOperation.tool_name == "submit_generation_batch",
                AgentOperation.status.in_(("waiting_media", "reconciling")),
            )
            .order_by(AgentOperation.updated_at, AgentOperation.id)
            .limit(limit)
        ).all()
    collected = 0
    for identifier in ids:
        try:
            with factory.begin() as session:
                parent = session.execute(
                    select(AgentOperation.run_id, AgentTask.execution_target)
                    .join(
                        AgentTask,
                        AgentTask.id == AgentOperation.task_id,
                    )
                    .where(AgentOperation.id == identifier)
                ).one_or_none()
                if parent is None:
                    continue
                if parent.execution_target and parent.execution_target.get("project_id"):
                    session.scalar(
                        select(Project)
                        .where(Project.id == int(parent.execution_target["project_id"]))
                        .with_for_update()
                    )
                rows = lock_run(session, parent.run_id, skip_locked=True)
                if rows is None:
                    continue
                _project, conversation, run = rows
                operation = session.scalar(
                    select(AgentOperation)
                    .where(
                        AgentOperation.id == identifier,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if operation.status not in {"waiting_media", "reconciling"}:
                    continue
                collected += int(
                    settle_batch_locked(
                        session, conversation, task_for_run(session, run), run, operation, settings
                    )
                )
                operation.updated_at = utcnow()
        except SQLAlchemyError as error:
            logger.warning(
                "Assistant media settlement unavailable; operation_id=%s error=%s",
                identifier,
                type(error).__name__,
            )
    return collected
