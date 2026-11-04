"""Read native adoption proofs and bind archived canvas outputs through native services."""

from copy import deepcopy

from short_drama.core.exceptions import Conflict
from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
from short_drama.service.assistant.operations.authorization import require_receipt_capability_locked
from short_drama.service.assistant.operations.image import image_adoption_state, image_target


def native_adoption_changes_locked(session, task, record, target):
    if target["kind"] == "node":
        return _bind_canvas(session, task, record, target)
    if task.service_type == "image":
        return _image_changes(session, task, target)
    return _video_changes(session, task, record, target)


def _image_changes(session, task, target):
    groups = BusinessInvocationDAO(session).generated_image_groups(
        task.initiated_by, task.project_id, {task.id}
    )
    proof = next(
        (
            group.source_context
            for group in groups
            if group.source_context.get("assistant_adoption_snapshot")
        ),
        None,
    )
    if proof is None:
        raise Conflict("生成图片尚未采用，已保存产物保留在候选中")
    _, row = image_target(
        session, task.initiated_by, task.project_id, target["kind"], target["id"], lock=True
    )
    after = image_adoption_state(session, row, target["kind"])
    if after != proof["assistant_adoption_snapshot"]:
        raise Conflict("生成后图片绑定已有变化，不能归为本次采用")
    changes = [
        {
            "kind": target["kind"],
            "id": target["id"],
            "media_role": "image",
            "before": deepcopy(target["before"]),
            "after": after,
        }
    ]
    transition = proof.get("assistant_storyboard_transition")
    if transition:
        changes.append(
            {
                "kind": "storyboard_document",
                "id": str(task.project_id),
                "before": {"row_version": transition["before"]},
                "after": {"row_version": transition["after"]},
            }
        )
    return changes


def _video_changes(session, task, record, target):
    from short_drama.service.creation.storyboard.videos.video_admission import _video_state

    proof = (record.response_data or {}).get("shot_video_auto_apply") or {}
    if proof.get("status") != "applied":
        raise Conflict("生成视频尚未采用，已保存产物保留在候选中")
    _, row = image_target(
        session, task.initiated_by, task.project_id, "shot", target["id"], lock=True
    )
    after = _video_state(session, row)
    if (
        after["row_version"] != proof["row_version"]
        or (after["video"] or {}).get("media_id") != proof["media_id"]
    ):
        raise Conflict("生成后视频绑定已有变化，不能归为本次采用")
    return [
        {
            "kind": "shot",
            "id": target["id"],
            "media_role": "video",
            "before": deepcopy(target["before"]),
            "after": after,
        },
        {
            "kind": "storyboard_document",
            "id": str(task.project_id),
            "before": {"row_version": proof["before_storyboard_version"]},
            "after": {"row_version": proof["storyboard_version"]},
        },
    ]


def _bind_canvas(session, task, record, target):
    from short_drama.schemas.canvas.generation.generation import CanvasTaskBindOperation
    from short_drama.service.assistant.operations.canvas_media import (
        _canvas,
        _node_state,
        canvas_target_matches,
    )
    from short_drama.service.canvas.document.canvas import CanvasService
    from short_drama.service.canvas.generation.tasks.task import CanvasTaskService

    if task.status != "succeeded":
        raise Conflict("画布媒体未全部保存，已保存产物保留在原生任务中")
    canvas = _canvas(session, task.initiated_by, target)
    native, service = CanvasService(session), CanvasTaskService(session)
    binding = service.tasks.binding(task.id)
    if binding is None:
        raise Conflict("画布生成任务关联缺失，不能绑定到节点")
    # 即使节点已被人工修改，真实产物也先交付私人任务和素材；仅绑定事务可回滚。
    outputs = service.materialize_locked(canvas, binding, task)
    if not outputs or len(outputs) != (record.response_data or {}).get("expected_count", 1):
        raise Conflict("已保存画布产物数量与受理目标不一致")
    require_receipt_capability_locked(
        session, session.info["business_media_receipts"][task.id], "workbench_write"
    )
    if not canvas_target_matches(session, task.initiated_by, target, task.service_type, task.id):
        raise Conflict("节点或私人参数已变化，产物已保留，未覆盖节点")
    before_revision = str(canvas.row_version)
    with session.begin_nested():
        receipt = service.bind_locked(
            CanvasTaskBindOperation.model_validate(
                {
                    "op_id": f"business-media-bind:{record.id}",
                    "params": {
                        "canvas_id": canvas.source_key,
                        "task_id": str(task.id),
                        "node_id": target["id"],
                        "output_index": min(row.result_index for row in outputs),
                    },
                }
            )
        )
        if receipt["result"]["bindingStatus"] != "bound":
            raise Conflict("原节点已删除或替换，未重新应用旧回执")
        after = _node_state(
            session, native, canvas, target["id"], native._document(canvas, private=True, lock=True)
        )
    return [
        {
            "kind": "canvas_node",
            "id": target["id"],
            "media_role": "canvas_binding",
            "adopted": True,
            "before": deepcopy(target["before"]),
            "after": after,
        },
        {
            "kind": "canvas_document",
            "id": canvas.source_key,
            "before": {"row_version": before_revision},
            "after": {"row_version": str(canvas.row_version)},
        },
    ]
