"""Reverse native media bindings while retaining generated files and immutable history."""

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import BusinessError, Conflict
from short_drama.dao.project_storyboard_dao import advance_shot_version, advance_storyboard_version
from short_drama.domain import Asset, Project, ShotImage, ShotVideo
from short_drama.domain.agent_media import AgentMediaRequest
from short_drama.domain.agent_task import AgentOperation
from short_drama.domain.native_voice import CharacterVoice
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.asset_service import AssetService
from short_drama.service.asset_version_service import AssetVersionService
from short_drama.service.assistant_image_service import image_adoption_state, image_target
from short_drama.service.assistant_shot_video_service import _video_state
from short_drama.service.assistant_undo_chain_service import (
    advance_snapshot,
    advance_target_version,
)
from short_drama.service.assistant_voice_service import voice_binding_state
from short_drama.service.base import utcnow
from short_drama.service.image_group_service import ImageGroupService
from short_drama.service.shot_image_service import ShotImageService
from short_drama.service.shot_media_service import recycle_snapshot
from short_drama.service.shot_video_service import ShotVideoService


def preview_media_change(
    service: Any,
    project: Project,
    operation: AgentOperation,
    change: dict[str, Any],
    receipts: list,
) -> tuple[str | None, dict[str, Any] | None, set[str]]:
    session, owner = service.session, service._actor().user_id
    kind = "asset" if change["kind"] == "character_voice" else change["kind"]
    try:
        _project, row = image_target(session, owner, project.id, kind, change["id"], lock=True)
    except BusinessError:
        return "原媒体目标已不可用", None, set()
    role = change.get("media_role")
    if change["kind"] == "character_voice":
        current = voice_binding_state(session, project.id, row.id, lock=True)
        intent = session.scalar(
            select(AgentMediaRequest).where(
                AgentMediaRequest.operation_id == operation.id,
                AgentMediaRequest.media_kind == "audio",
                AgentMediaRequest.frozen_target["id"].as_string() == str(row.id),
            )
        )
        version = (intent.frozen_target if intent else {}).get("row_version")
        if version is not None:
            version = advance_target_version(version, change, receipts)
        if version is None or str(row.row_version) != version:
            return (
                "角色正文或版本已有后续变化",
                {**current, "target_version": str(row.row_version)},
                set(),
            )
        exhausted = int(current["row_version"]) == UINT64_MAX
    elif role == "image":
        current = image_adoption_state(session, row, kind)
        if "image_groups" not in (change.get("before") or {}):
            return "旧图片回执缺少图片组差异，不能安全回退", current, set()
        exhausted = row.row_version == UINT64_MAX or any(
            int(group["primary_version"]) == UINT64_MAX
            for group in current["image_groups"].values()
        )
    elif role == "video" and kind == "shot":
        current = _video_state(session, row)
        exhausted = row.row_version == UINT64_MAX
    else:
        return "旧媒体回执缺少可证明的采用差异", None, set()
    if current != advance_snapshot(change, receipts, operation.id):
        return "媒体绑定、图片组或版本已有后续变化", current, set()
    if exhausted or kind == "shot" and project.storyboard_version == UINT64_MAX:
        return "媒体目标或作品版本已达到上限", current, set()
    return None, current, set()


def _restore_shot_media(
    session: Session, project: Project, row: Any, before: dict, role: str
) -> None:
    model, native = (
        (ShotImage, ShotImageService(session))
        if role == "image"
        else (ShotVideo, ShotVideoService(session))
    )
    existing = session.scalar(select(model).where(model.shot_id == row.id).with_for_update())
    binding = before["image" if role == "image" else "video"]
    if binding is None:
        if existing is not None:
            recycle_snapshot(session, existing, "discarded")
            session.delete(existing)
        row.updated_at, row.updated_by = utcnow(), None
        advance_shot_version(row)
        advance_storyboard_version(project)
        session.flush()
        return
    values = {
        key: int(value) if key.endswith("_id") and value is not None else deepcopy(value)
        for key, value in binding.items()
        if key != "id"
    }
    values.update(project_id=project.id, shot_id=row.id)
    prior_version = row.row_version
    native._write_confirmed(values, row, existing=existing, historical=True)
    if row.row_version == prior_version:
        advance_shot_version(row)
        advance_storyboard_version(project)
    session.flush()


def _restore_image_groups(session: Session, row: Any, kind: str, before: dict) -> None:
    native = ImageGroupService(session)
    groups = list(native.groups.for_target(kind, row.id, lock=True))
    previous = before["image_groups"]
    current = image_adoption_state(session, row, kind)["image_groups"]
    changed = [group for group in groups if previous.get(str(group.id)) != current[str(group.id)]]
    for group in changed:
        group.active_slot = None
    session.flush()
    for group in changed:
        state = previous.get(str(group.id))
        if state is not None:
            group.active_slot = 1 if state["is_current"] else None
            group.primary_index, group.primary_mode = state["primary_index"], state["primary_mode"]
        group.primary_version += 1
    session.flush()


def restore_media_locked(
    service: Any,
    project: Project,
    changes: dict[str, dict[str, Any]],
    selected: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    session, owner = service.session, service._actor().user_id
    inverse, refs = [], []
    for key in sorted(selected):
        change, role = changes[key], changes[key].get("media_role")
        kind = "asset" if change["kind"] == "character_voice" else change["kind"]
        _project, row = image_target(session, owner, project.id, kind, change["id"], lock=True)
        native = ImageGroupService(session)
        native.target(kind, row.id)
        before = change["before"]
        if change["kind"] == "character_voice":
            snapshot = voice_binding_state(session, project.id, row.id, lock=True)
            binding = session.get(CharacterVoice, (project.id, row.id), with_for_update=True)
            if binding is None:
                raise Conflict("角色音色绑定已不可用")
            if before["media_id"] is not None:
                native._validate_media(int(before["media_id"]), "audio")
            binding.record_id = int(before["record_id"]) if before["record_id"] else None
            binding.media_id = int(before["media_id"]) if before["media_id"] else None
            binding.row_version += 1
            session.flush()
            after = voice_binding_state(session, project.id, row.id, lock=True)
            ref = {
                "voice_version": after["row_version"],
                "record_id": after["record_id"],
                "media_id": after["media_id"],
                "media_role": "character_voice",
                "media_kind": "audio",
            }
        else:
            snapshot = (
                image_adoption_state(session, row, kind)
                if role == "image"
                else _video_state(session, row)
            )
            if kind == "asset":
                if before["media_id"] is not None:
                    native._validate_media(int(before["media_id"]), "image")
                AssetService(session)._apply_update(
                    row,
                    {
                        "media_id": int(before["media_id"]) if before["media_id"] else None,
                        "model_id": int(before["model_id"]) if before["model_id"] else None,
                        "state": before["state"],
                        "row_version": row.row_version + 1,
                    },
                )
                AssetVersionService(session).append_locked(row)
            else:
                _restore_shot_media(session, project, row, before, role)
            if role == "image":
                _restore_image_groups(session, row, kind, before)
                after = image_adoption_state(session, row, kind)
                active = next(
                    (
                        (identifier, group)
                        for identifier, group in after["image_groups"].items()
                        if group["is_current"]
                    ),
                    None,
                )
                primary = (
                    next(
                        (
                            item
                            for item in native.groups.items(int(active[0]), lock=True)
                            if item.output_index == active[1]["primary_index"]
                        ),
                        None,
                    )
                    if active
                    else None
                )
                ref = {
                    "image_group_id": active[0] if active else None,
                    "image_slot_id": str(primary.id) if primary else None,
                    "primary_version": active[1]["primary_version"] if active else None,
                    "item_version": str(primary.item_version) if primary else None,
                }
            else:
                after, ref = _video_state(session, row), {}
            media = (
                after.get("media_id")
                if kind == "asset"
                else (after["image" if role == "image" else "video"] or {}).get("media_id")
            )
            ref.update(media_id=media, media_kind=role)
        inverse.append(
            {
                "kind": change["kind"],
                "id": change["id"],
                "before": snapshot,
                "after": after,
                **({"media_role": role} if role else {}),
            }
        )
        refs.append(
            {
                "kind": kind,
                "id": str(row.id),
                "project_id": str(project.id),
                "name": row.name if isinstance(row, Asset) else row.title,
                "row_version": str(row.row_version),
                "status": "updated",
                **ref,
            }
        )
    return inverse, refs
