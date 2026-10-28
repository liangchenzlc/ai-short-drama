"""Explicit inverse batches use current locks and differences, never a whole-project restore."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.core.events import ACTIVE
from short_drama.agent.task.state import task_event
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.dao.assistant.undo.undo import AgentUndoDAO
from short_drama.dao.creation.assets.management.library import AssetLibraryDAO
from short_drama.dao.creation.storyboard.management.storyboard import (
    advance_shot_version,
    advance_storyboard_version,
)
from short_drama.domain import Asset, AsyncTask, Project, ShotDialogue, ShotScript
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.domain.creation.assembly.document.assembly import ProjectAssemblyClip
from short_drama.schemas.assistant.undo.undo import (
    BatchUndoPreview,
    BatchUndoRead,
    BatchUndoRequest,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.service.assistant.context.attachment import attachment_key
from short_drama.service.assistant.operations.standard import AssistantStandardService
from short_drama.service.assistant.undo.canvas_undo import (
    canvas_undo_context,
    preview_canvas_change,
    restore_canvas_locked,
)
from short_drama.service.assistant.undo.media_undo import (
    preview_media_change,
    restore_media_locked,
)
from short_drama.service.assistant.undo.sound_undo import (
    preview_sound_change,
    restore_sound_locked,
)
from short_drama.service.assistant.undo.undo_chain import (
    advance_document,
    advance_snapshot,
    record_restorations,
)
from short_drama.service.base import utcnow
from short_drama.service.creation.assets.management.assets import AssetService
from short_drama.service.creation.assets.management.version import AssetVersionService
from short_drama.service.creation.sound.voices.voices import NativeVoiceService
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


def undo_read(row):
    return BatchUndoRead.model_validate(row, from_attributes=True)


def item_name(change: dict, changes: dict) -> str:
    snapshot = change.get("after") or change.get("before") or {}
    if change["kind"] == "canvas_edge":
        names = []
        for field in ("fromNodeId", "toNodeId"):
            identifier = snapshot[field]
            node = changes.get(f"canvas_node:{identifier}", {})
            node_snapshot = node.get("after") or node.get("before") or {}
            names.append(node_snapshot.get("document", {}).get("title") or identifier)
        return "连线：" + " → ".join(names)
    if change["kind"] == "sound_line":
        return f"{snapshot.get('character', '台词')} · {snapshot.get('text', change['id'])[:40]}"
    return str(
        snapshot.get("name")
        or snapshot.get("title")
        or snapshot.get("document", {}).get("title")
        or change["id"]
    )


def locked_source(service, identifier):
    session = service.session
    operation = session.get(AgentOperation, parse_identifier(identifier))
    if operation is None:
        raise NotFound("操作不存在")
    task = AgentTaskDAO(session).task(operation.task_id)
    if task is None:
        raise NotFound("任务不存在")
    conversation = service._writable(task.conversation_id)
    task = AgentTaskDAO(session).task(task.id, lock=True)
    operation = session.scalar(
        select(AgentOperation)
        .where(AgentOperation.id == operation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return conversation, task, operation


def preview_locked(service, conversation, task, operation):
    session = service.session
    dao = AgentUndoDAO(session)
    receipts = dao.receipts(operation.id)
    source_target = {key: (operation.result or {}).get(key) for key in ("project_id", "canvas_id")}
    history = [
        receipt
        for receipt, source in dao.task_receipts(conversation.id, task.id)
        if source.id >= operation.id
        and {key: (source.result or {}).get(key) for key in source_target} == source_target
    ]
    undone = {key for row in receipts for key in row.selected_keys}
    busy = (
        session.scalar(
            select(AgentRun.id)
            .where(AgentRun.conversation_id == conversation.id, AgentRun.status.in_(ACTIVE))
            .limit(1)
        )
        is not None
    )
    target = (
        {"project_id": (operation.result or {}).get("project_id")}
        if operation.tool_name
        in {
            "apply_standard_patch",
            "apply_canvas_patch",
            "set_image_group_primary",
            "submit_generation_batch",
        }
        else task.execution_target or {}
    )
    project = session.scalar(
        select(Project)
        .where(
            Project.id == int(target.get("project_id") or 0),
            Project.owner_user_id == conversation.owner_user_id,
            Project.archived_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    project_busy = (
        project is not None
        and session.scalar(
            select(AsyncTask.id)
            .where(
                AsyncTask.project_id == project.id,
                AsyncTask.status.not_in(("succeeded", "failed", "cancelled")),
            )
            .limit(1)
        )
        is not None
    )
    storyboard = ProjectStoryboardService(session)
    items, states, rows = [], [], {}
    changes = operation.changes or []
    grouped_image = any(change.get("media_role") == "image" for change in changes)
    by_key = {
        f"{change['kind']}:{change['id']}": change
        for change in changes
        if change["kind"] not in {"sound_document", "canvas_document", "storyboard_document"}
        and not (grouped_image and change["kind"] == "image_group")
    }
    canvas_context = (
        canvas_undo_context(session, project, operation, history)
        if project is not None and (operation.result or {}).get("canvas_id")
        else None
    )
    rows["canvas_context"] = canvas_context
    shot_keys = {key for key, change in by_key.items() if change["kind"] == "shot"}
    for key, change in by_key.items():
        kind, identifier = change["kind"], change["id"]
        after, before = advance_snapshot(change, history, operation.id), change.get("before")
        reason, current, requires = None, None, set()
        if key in undone:
            reason = "这项内容已经撤销"
        elif operation.status != "succeeded" and not (
            operation.tool_name == "submit_generation_batch"
            and operation.status in {"failed", "conflict"}
        ):
            reason = "只能撤销已完整结算的批次"
        elif busy or project_busy:
            reason = "请等待当前工具完整结束并停止任务后再撤销"
        elif project is None:
            reason = "写入目标已不可用"
        elif kind in {"canvas_node", "canvas_edge"} and operation.tool_name in {
            "apply_canvas_patch",
            "submit_generation_batch",
        }:
            if project.workspace_mode != "infinite_canvas":
                reason = "原批次不属于画布目标"
            elif operation.tool_name == "submit_generation_batch" and not change.get("adopted"):
                reason = "本项未采用媒体，不能作为已采用成果回退"
            else:
                reason, current, requires = preview_canvas_change(canvas_context, change, by_key)
        elif kind == "sound_line" and operation.tool_name in {
            "apply_standard_patch",
            "submit_generation_batch",
        }:
            reason, current, requires = preview_sound_change(
                service, project, change, history, changes, operation_id=operation.id
            )
        elif operation.tool_name in {"submit_generation_batch", "set_image_group_primary"}:
            reason, current, requires = preview_media_change(
                service, project, operation, change, history
            )
        elif operation.tool_name != "apply_standard_patch" or kind not in {"asset", "shot"}:
            reason = "这类成果的逆向操作尚未接入"
        elif project.workspace_mode != "standard":
            reason = "原批次不属于标准创作目标"
        else:
            model = Asset if kind == "asset" else ShotScript
            row = session.scalar(
                select(model)
                .where(model.id == int(identifier), model.project_id == project.id)
                .with_for_update()
                .execution_options(populate_existing=True)
                .execution_options(
                    **(
                        {"assistant_undo_operation_id": operation.id}
                        if kind == "asset" and after and after.get("deleted")
                        else {}
                    )
                )
            )
            if row is None:
                reason = "原对象已不可用"
            else:
                rows[key] = row
                current = AssistantStandardService._snapshot(row, kind, storyboard)
                if not isinstance(after, dict) or "media_state" not in after:
                    reason = "旧回执缺少媒体版本，不能证明可以安全撤销"
                elif current != after:
                    reason = "对象、关联、媒体或版本已有后续变化"
                elif row.row_version == UINT64_MAX:
                    reason = "对象版本已达到上限"
                elif kind == "shot":
                    requires.update(shot_keys - {key})
                    expected_storyboard = advance_document(
                        {"row_version": str((operation.result or {}).get("storyboard_version"))},
                        "storyboard_document",
                        str(project.id),
                        history,
                        operation.id,
                    )
                    if str(project.storyboard_version) != expected_storyboard["row_version"]:
                        reason = "分镜列表已有后续变化，请保留当前顺序"
                    elif project.storyboard_version == UINT64_MAX:
                        reason = "分镜列表版本已达到上限"
                    elif (
                        before is None
                        and session.scalar(
                            select(ProjectAssemblyClip.id)
                            .where(ProjectAssemblyClip.shot_id == row.id)
                            .limit(1)
                        )
                        is not None
                    ):
                        reason = "新建分镜已被成片引用"
                    else:
                        for asset_id in (before or {}).get("asset_ids", []):
                            asset = session.get(Asset, int(asset_id), with_for_update=True)
                            dependency = f"asset:{asset_id}"
                            if asset is None or asset.deleted_at is not None:
                                restore = by_key.get(dependency, {}).get("before")
                                if restore and not restore["deleted"]:
                                    requires.add(dependency)
                                else:
                                    reason = "原关联素材已不可用"
                elif before is None:
                    for shot_id in AssetLibraryDAO(session).active_shot_ids(project.id, row.id):
                        dependency = f"shot:{shot_id}"
                        if dependency in by_key and by_key[dependency].get("before") is None:
                            requires.add(dependency)
                        else:
                            reason = "新建素材仍被后续分镜引用"
        ref = next(
            (ref for ref in operation.result_refs or [] if str(ref.get("id")) == str(identifier)),
            {},
        )
        name = item_name(change, by_key)
        if name == str(identifier):
            name = ref.get("name") or name
        items.append(
            {
                "key": key,
                "kind": kind,
                "id": str(identifier),
                "name": name,
                "allowed": reason is None,
                "reason": reason,
                "requires": sorted(requires),
            }
        )
        states.append({"key": key, "current": current})
    # Dependencies can themselves be protected; never offer a misleading safe subset.
    for _ in range(len(items)):
        blocked = {item["key"] for item in items if not item["allowed"]}
        changed = False
        for item in items:
            if item["allowed"] and blocked.intersection(item["requires"]):
                item.update(allowed=False, reason="必须同时撤销的关联内容已有冲突")
                changed = True
        if not changed:
            break
    version = digest(
        {
            "operation_id": str(operation.id),
            "items": items,
            "states": states,
            "receipts": [str(row.id) for row in history],
            "target": target,
            "storyboard_version": str(project.storyboard_version) if project else None,
        }
    )
    return (
        BatchUndoPreview(
            operation_id=operation.id,
            preview_hash=version,
            items=items,
            receipts=[undo_read(row) for row in receipts],
        ),
        rows,
        by_key,
        project,
    )


def preview(service, identifier):
    with service._transaction():
        conversation, task, operation = locked_source(service, identifier)
        result, _rows, _changes, _project = preview_locked(service, conversation, task, operation)
        return result


def restore_standard_locked(service, project, rows, changes, selected, receipt_id):
    session, now = service.session, utcnow()
    storyboard = ProjectStoryboardService(session)
    owner = service._actor().user_id
    result, inverse = [], []
    shot_keys = [key for key in selected if changes[key]["kind"] == "shot"]
    # Move changed rows out of the active ordering range before restoring positions.
    active = storyboard.list_active_for_update(project.id) if shot_keys else []
    maximum = max((row.position for row in active), default=0)
    if maximum + len(shot_keys) > 2**32 - 1:
        raise Conflict("没有足够的临时分镜排序空间")
    snapshots = {
        key: AssistantStandardService._snapshot(rows[key], changes[key]["kind"], storyboard)
        for key in selected
    }
    for index, key in enumerate(shot_keys, maximum + 1):
        rows[key].position = index
    session.flush()
    # Restored assets precede their links; removed new assets follow removed links.
    ordered = sorted(
        selected,
        key=lambda key: (
            0
            if changes[key]["kind"] == "asset" and changes[key]["before"] is not None
            else 2
            if changes[key]["kind"] == "asset"
            else 1,
            key,
        ),
    )
    for key in ordered:
        change, row = changes[key], rows[key]
        kind, before = change["kind"], change["before"]
        if kind == "asset":
            fields = (
                {
                    field: deepcopy(before[field])
                    for field in (
                        "name",
                        "kind",
                        "label",
                        "description",
                        "prompt",
                        "tags",
                        "scene_time",
                        "state",
                    )
                }
                if before
                else {}
            )
            assets = AssetService(session)
            assets._validate_update(row, fields)
            fields.update(
                deleted_at=now if before is None or before["deleted"] else None,
                row_version=row.row_version + 1,
            )
            assets._apply_update(row, fields)
            if before is not None:
                AssetVersionService(session).append_locked(row)
        else:
            row.deleted_at = now if before is None or before["deleted"] else None
            if before:
                for field in (
                    "title",
                    "script",
                    "duration_ms",
                    "shot_size",
                    "image_settings",
                    "video_prompt",
                    "video_settings",
                    "position",
                ):
                    setattr(row, field, deepcopy(before[field]))
            row.updated_at, row.updated_by = now, owner
            advance_shot_version(row)
            storyboard.dao.replace_assets(
                row, [int(value) for value in before["asset_ids"]] if before else [], now
            )
            if before and not before["deleted"]:
                dialogue = session.get(ShotDialogue, row.id, with_for_update=True)
                document = before["dialogue"]["document"]
                if document != (
                    dialogue.document if dialogue else {"lines": [], "reviewed": False}
                ):
                    NativeVoiceService(session, service.settings).save_dialogue_locked(
                        project.id,
                        row.id,
                        {
                            "row_version": dialogue.row_version if dialogue else 0,
                            "request_id": f"assistant-undo:{receipt_id}:{row.id}",
                            "document": document,
                        },
                        skip_unchanged=True,
                    )
        session.flush()
        after = AssistantStandardService._snapshot(row, kind, storyboard)
        inverse.append({"kind": kind, "id": str(row.id), "before": snapshots[key], "after": after})
        result.append(
            {
                "kind": kind,
                "id": str(row.id),
                "project_id": str(project.id),
                "name": row.name if kind == "asset" else row.title,
                "row_version": str(row.row_version),
                "status": "deleted" if row.deleted_at else "updated",
            }
        )
    if shot_keys:
        advance_storyboard_version(project)
    session.flush()
    return inverse, result


def apply(service, identifier, payload, idempotency_key):
    values = BatchUndoRequest.model_validate(service._payload(BatchUndoRequest, payload))
    key = attachment_key(idempotency_key)
    request_hash = digest({"operation_id": str(identifier), **values.model_dump(mode="json")})
    with service._transaction():
        conversation, task, operation = locked_source(service, identifier)
        dao = AgentUndoDAO(service.session)
        prior = dao.request(conversation.id, key)
        if prior is not None:
            if prior.request_hash != request_hash:
                raise Conflict("此请求身份已经用于其他撤销范围")
            return undo_read(prior)
        current, rows, changes, project = preview_locked(service, conversation, task, operation)
        if current.preview_hash != values.preview_hash:
            raise WorkflowError("assistant_undo_stale", "撤销预览已变化，输入保留，请重新核对", 409)
        selected = set(values.selected_keys)
        items = {item.key: item for item in current.items}
        if any(key not in items or not items[key].allowed for key in selected):
            raise WorkflowError(
                "assistant_undo_conflict", "选择中含冲突或不存在的内容，未执行撤销", 409
            )
        if any(not set(items[key].requires).issubset(selected) for key in selected):
            raise WorkflowError(
                "assistant_undo_dependencies", "请明确同时选择必须撤销的关联内容", 409
            )
        receipt_id = next_id()
        storyboard_before = str(project.storyboard_version)
        if rows["canvas_context"] is not None:
            inverse, refs = restore_canvas_locked(
                service, project, rows["canvas_context"], changes, selected
            )
        elif operation.tool_name in {"submit_generation_batch", "set_image_group_primary"}:
            sound = {key for key in selected if changes[key]["kind"] == "sound_line"}
            inverse, refs = restore_media_locked(service, project, changes, selected - sound)
            if sound:
                sound_inverse, sound_refs = restore_sound_locked(
                    service, project, changes, sound, receipt_id
                )
                inverse.extend(sound_inverse)
                refs.extend(sound_refs)
        else:
            standard = {key for key in selected if changes[key]["kind"] in {"asset", "shot"}}
            sound = selected - standard
            inverse, refs = (
                restore_standard_locked(service, project, rows, changes, standard, receipt_id)
                if standard
                else ([], [])
            )
            if sound:
                sound_inverse, sound_refs = restore_sound_locked(
                    service, project, changes, sound, receipt_id
                )
                inverse.extend(sound_inverse)
                refs.extend(sound_refs)
        if str(project.storyboard_version) != storyboard_before:
            inverse.append(
                {
                    "kind": "storyboard_document",
                    "id": str(project.id),
                    "before": {"row_version": storyboard_before},
                    "after": {"row_version": str(project.storyboard_version)},
                }
            )
        record_restorations(operation, changes, selected, current.receipts, inverse)
        if operation.tool_name in {"submit_generation_batch", "set_image_group_primary"}:
            for ref in refs:
                original = next(
                    (
                        item
                        for item in operation.result_refs
                        if (item.get("kind"), item.get("id")) == (ref["kind"], ref["id"])
                    ),
                    {},
                )
                ref["name"] = original.get("name") or ref["name"]
                if ref["kind"] == "canvas_node":
                    ref["media_id"] = None
        receipt = dao.add(
            id=receipt_id,
            conversation_id=conversation.id,
            task_id=task.id,
            operation_id=operation.id,
            request_key=key,
            request_hash=request_hash,
            preview_hash=values.preview_hash,
            selected_keys=sorted(selected),
            changes=inverse,
            result_refs=refs,
            created_at=utcnow(),
        )
        task.row_version += 1
        task.updated_at = utcnow()
        task_event(
            service.session,
            conversation,
            task,
            "operation.undo.completed",
            operation_id=str(operation.id),
            undo_id=str(receipt.id),
            result_refs=refs,
            receipt=undo_read(receipt).model_dump(mode="json"),
        )
        return undo_read(receipt)
