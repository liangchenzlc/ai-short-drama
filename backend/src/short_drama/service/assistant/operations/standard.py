"""Atomic standard patches called only after the executor establishes a trusted write scope."""

import hashlib
import json
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.dao.creation.assets.management.library import AssetLibraryDAO
from short_drama.dao.creation.storyboard.management.storyboard import (
    advance_shot_version,
    advance_storyboard_version,
    require_shot_version,
)
from short_drama.domain import Asset, Project, ShotDialogue, ShotScript
from short_drama.domain.creation.sound.voices.voices import CharacterVoice
from short_drama.domain.creation.storyboard.images.image import ShotImage
from short_drama.domain.creation.storyboard.videos.video import ShotVideo
from short_drama.schemas.assistant.operations.standard import StandardPatch
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.creation.storyboard.management.storyboard import StoryboardGeneratedShot
from short_drama.service.assistant.operations.dialogue import (
    SOUND_ACTIONS,
    apply_sound_lines_locked,
)
from short_drama.service.base import BaseService, utcnow
from short_drama.service.creation.assets.management.assets import AssetService
from short_drama.service.creation.assets.management.library import AssetLibraryService
from short_drama.service.creation.sound.voices.voices import NativeVoiceService
from short_drama.service.creation.storyboard.management.script import ShotScriptService
from short_drama.service.creation.storyboard.management.storyboard import (
    DEFAULT_IMAGE_SETTINGS,
    ProjectStoryboardService,
)


def patch_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class AssistantStandardService(BaseService):
    model = Project

    def __init__(self, session: Session, settings=None):
        super().__init__(session)
        self.settings = settings if settings is not None else Settings()

    def apply_locked(
        self,
        project_id: int | str,
        payload: StandardPatch | dict[str, Any],
        *,
        owner_user_id: int | str,
        operation_id: int | str,
        allowed_asset_ids: Iterable[int | str] = (),
        allowed_shot_ids: Iterable[int | str] = (),
        allowed_sound_line_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Caller owns the transaction, authorization, durable operation and its replay guard.

        The allowed IDs must come from validated task subjects/results, never model arguments.
        A failed batch must roll back to the caller's savepoint with its operation receipt.
        """
        if not self.session.in_transaction():
            raise BusinessError("Standard patch requires the caller's transaction")
        batch = StandardPatch.model_validate(self._payload(StandardPatch, payload))
        project_id, owner_user_id = parse_identifier(project_id), parse_identifier(owner_user_id)
        operation_id = parse_identifier(operation_id)
        creation = self.session.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.owner_user_id == owner_user_id,
                Project.workspace_mode == "standard",
                Project.archived_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if creation is None:
            raise NotFound("标准写入目标不存在或无权访问")
        storyboard = ProjectStoryboardService(self.session)
        storyboard.lock_project(creation.id, batch.storyboard_version)
        allowed = {
            "asset": {parse_identifier(value) for value in allowed_asset_ids},
            "shot": {parse_identifier(value) for value in allowed_shot_ids},
        }
        temporary = {}
        changes = {}

        def resolve(reference: str, kind: str) -> Asset | ShotScript:
            if reference.startswith("$"):
                row = temporary.get(reference[1:])
                if (
                    row is None
                    or not isinstance(row, Asset if kind == "asset" else ShotScript)
                    or row.deleted_at is not None
                ):
                    raise WorkflowError("assistant_reference_invalid", "批次临时引用无效", 422)
                return row
            identifier = parse_identifier(reference)
            if identifier not in allowed[kind]:
                raise WorkflowError(
                    "assistant_write_scope", "只能修改或关联获准主体及本任务成果", 422
                )
            model = Asset if kind == "asset" else ShotScript
            row = self.session.scalar(
                select(model)
                .where(
                    model.id == identifier,
                    model.project_id == creation.id,
                    model.deleted_at.is_(None),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise NotFound("批次目标已移除或不属于写入作品")
            return row

        def remember(row: Asset | ShotScript, kind: str, before: dict | None) -> None:
            key = (kind, row.id)
            if key not in changes:
                changes[key] = {"kind": kind, "id": str(row.id), "before": before}
            changes[key]["after"] = self._snapshot(row, kind, storyboard)
            if kind == "shot" and row.deleted_at is not None and before is not None:
                changes[key]["after"]["dialogue"] = deepcopy(before["dialogue"])

        for index, action in enumerate(batch.actions):
            if action.action in SOUND_ACTIONS:
                continue
            if action.action == "create_asset":
                row, _link = AssetLibraryService(self.session).create_locked(
                    "project",
                    creation.id,
                    action.values,
                    f"assistant:{operation_id}:{index}",
                    patch_hash(action.values.model_dump(mode="json")),
                )
                temporary[action.key] = row
                remember(row, "asset", None)
            elif action.action == "update_asset":
                row = resolve(action.asset, "asset")
                before = self._snapshot(row, "asset", storyboard)
                AssetService(self.session).apply_patch_locked(
                    row,
                    action.patch.model_dump(exclude_unset=True, exclude={"row_version"}),
                    action.patch.row_version,
                )
                remember(row, "asset", before)
            elif action.action == "create_shot":
                asset_ids = [resolve(ref, "asset").id for ref in action.assets]
                values = action.values
                row = storyboard.create_generated_locked(
                    creation,
                    [
                        StoryboardGeneratedShot(
                            title=values.title,
                            shot_size=values.shot_size,
                            script=values.script,
                            duration_ms=values.duration_ms,
                            asset_ids=asset_ids,
                        ).model_dump()
                    ],
                )[0]
                row.image_settings = values.image_settings.model_dump()
                row.video_prompt = values.video_prompt
                row.video_settings = values.video_settings.model_dump(exclude_none=True)
                row.creation_key = f"assistant:{operation_id}:{index}"
                row.creation_hash = patch_hash(action.model_dump(mode="json"))
                advance_storyboard_version(creation)
                temporary[action.key] = row
                self.session.flush()
                remember(row, "shot", None)
            elif action.action == "update_shot":
                row = resolve(action.shot, "shot")
                before = self._snapshot(row, "shot", storyboard)
                values = action.patch.model_dump(exclude_unset=True, exclude={"row_version"})
                if "image_settings" in values:
                    values["image_settings"] = action.patch.image_settings.model_dump()
                if "video_settings" in values:
                    values["video_settings"] = action.patch.video_settings.model_dump(
                        exclude_none=True
                    )
                ShotScriptService(self.session).apply_patch_locked(
                    creation, row, values, action.patch.row_version
                )
                remember(row, "shot", before)
            elif action.action == "set_shot_assets":
                row = resolve(action.shot, "shot")
                require_shot_version(row, action.row_version)
                before = self._snapshot(row, "shot", storyboard)
                identifiers = sorted(resolve(ref, "asset").id for ref in action.assets)
                current = storyboard.dao.asset_ids(row.id)
                if current != identifiers:
                    # A replacement also removes old links. Every removed object must be in scope.
                    newly_created = {
                        item.id for item in temporary.values() if isinstance(item, Asset)
                    }
                    if not set(current).issubset(allowed["asset"] | newly_created):
                        raise WorkflowError(
                            "assistant_related_scope",
                            "替换关联会移除未获准素材，请先明确关联范围",
                            409,
                        )
                    storyboard.dao.require_project_assets(creation.id, identifiers)
                    storyboard.dao.replace_assets(row, identifiers, utcnow())
                    row.updated_at, row.updated_by = utcnow(), owner_user_id
                    advance_shot_version(row)
                    advance_storyboard_version(creation)
                    self.session.flush()
                remember(row, "shot", before)
            elif action.action == "reorder_shots":
                rows = [resolve(ref, "shot") for ref in action.shots]
                for ref, row in zip(action.shots, rows, strict=True):
                    require_shot_version(row, action.versions[ref])
                positions = sorted(row.position for row in rows)
                changed = [
                    (row, position)
                    for row, position in zip(rows, positions, strict=True)
                    if row.position != position
                ]
                before = {
                    row.id: self._snapshot(row, "shot", storyboard) for row, _position in changed
                }
                maximum = max(
                    (row.position for row in storyboard.list_active_for_update(creation.id)),
                    default=0,
                )
                if maximum + len(changed) > 2**32 - 1:
                    raise Conflict("Insufficient temporary ordering space")
                for temporary_position, (row, _position) in enumerate(changed, maximum + 1):
                    row.position = temporary_position
                self.session.flush()
                for row, position in changed:
                    row.position = position
                    row.updated_at, row.updated_by = utcnow(), owner_user_id
                    advance_shot_version(row)
                    remember(row, "shot", before[row.id])
                if changed:
                    advance_storyboard_version(creation)
                    self.session.flush()
            elif action.action == "edit_dialogue":
                row = resolve(action.shot, "shot")
                require_shot_version(row, action.row_version)
                before = self._snapshot(row, "shot", storyboard)
                document = action.document.model_dump(mode="json")
                for line in document["lines"]:
                    line["character_id"] = str(resolve(line["character_id"], "asset").id)
                NativeVoiceService(self.session, self.settings).save_dialogue_locked(
                    creation.id,
                    row.id,
                    {
                        "row_version": action.dialogue_version,
                        "request_id": f"assistant:{operation_id}:{index}",
                        "document": document,
                    },
                    skip_unchanged=True,
                )
                remember(row, "shot", before)
            elif action.action == "delete_asset":
                row = resolve(action.asset, "asset")
                if row.row_version != action.row_version:
                    raise WorkflowError("asset_version_conflict", "素材已变化，请刷新后删除", 409)
                references = AssetLibraryDAO(self.session).active_shot_ids(creation.id, row.id)
                if references:
                    raise WorkflowError(
                        "assistant_delete_impact",
                        "素材仍被分镜引用，请先明确解除关联或删除范围",
                        409,
                        details={"shot_ids": [str(value) for value in references]},
                    )
                if row.row_version >= UINT64_MAX:
                    raise Conflict("Asset version exhausted")
                before = self._snapshot(row, "asset", storyboard)
                AssetService(self.session)._apply_update(
                    row, {"deleted_at": utcnow(), "row_version": row.row_version + 1}
                )
                remember(row, "asset", before)
            else:
                row = resolve(action.shot, "shot")
                require_shot_version(row, action.row_version)
                active = storyboard.list_active_for_update(creation.id)
                remaining = [item for item in active if item.id != row.id]
                compacted = [
                    (item, position)
                    for position, item in enumerate(remaining, 1)
                    if item.position != position
                ]
                maximum = max(item.position for item in active)
                if maximum + len(compacted) > 2**32 - 1:
                    raise Conflict("Insufficient temporary ordering space")
                before = self._snapshot(row, "shot", storyboard)
                storyboard.archive_active_locked([row])
                storyboard.dao.replace_assets(row, [], utcnow())
                remember(row, "shot", before)
                original = {
                    item.id: self._snapshot(item, "shot", storyboard)
                    for item, _position in compacted
                }
                for temporary_position, (item, _position) in enumerate(compacted, maximum + 1):
                    item.position = temporary_position
                self.session.flush()
                for item, position in compacted:
                    item.position = position
                    item.updated_at, item.updated_by = utcnow(), owner_user_id
                    advance_shot_version(item)
                    remember(item, "shot", original[item.id])
                advance_storyboard_version(creation)
                self.session.flush()
        self.session.flush()
        sound_result = (
            apply_sound_lines_locked(
                self.session,
                creation.id,
                batch,
                operation_id,
                set(allowed_sound_line_ids),
                self.settings,
                owner_user_id,
            )
            if any(action.action in SOUND_ACTIONS for action in batch.actions)
            else {"changes": [], "result_refs": [], "temporary_ids": {}}
        )
        return {
            "project_id": str(creation.id),
            "storyboard_version": str(creation.storyboard_version),
            "temporary_ids": {
                **{key: str(row.id) for key, row in temporary.items()},
                **sound_result["temporary_ids"],
            },
            **(
                {"sound_revision": sound_result["sound_revision"]}
                if "sound_revision" in sound_result
                else {}
            ),
            "result_refs": [
                {
                    "kind": item["kind"],
                    "id": item["id"],
                    "project_id": str(creation.id),
                    "name": item["after"]["name" if item["kind"] == "asset" else "title"],
                    "row_version": item["after"]["row_version"],
                    **(
                        {"dialogue_version": item["after"]["dialogue"]["row_version"]}
                        if item["kind"] == "shot"
                        else {}
                    ),
                    "status": "deleted"
                    if item["after"]["deleted"]
                    else "created"
                    if item["before"] is None
                    else "unchanged"
                    if item["before"] == item["after"]
                    else "updated",
                }
                for item in changes.values()
            ]
            + sound_result["result_refs"],
            "changes": [item for item in changes.values() if item["before"] != item["after"]]
            + sound_result["changes"],
        }

    @staticmethod
    def _snapshot(
        row: Asset | ShotScript, kind: str, storyboard: ProjectStoryboardService
    ) -> dict[str, Any]:
        fields = (
            ("name", "kind", "label", "description", "prompt", "tags", "scene_time", "state")
            if kind == "asset"
            else (
                "title",
                "script",
                "duration_ms",
                "shot_size",
                "image_settings",
                "video_prompt",
                "video_settings",
            )
        )
        result = {field: deepcopy(getattr(row, field)) for field in fields}
        result["row_version"] = str(row.row_version)
        result["position"] = row.position
        result["deleted"] = row.deleted_at is not None
        if kind == "asset":
            voice = storyboard.session.get(
                CharacterVoice, (row.project_id, row.id), with_for_update=True
            )
            result["media_state"] = {
                "media_id": str(row.media_id) if row.media_id is not None else None,
                "voice_version": str(voice.row_version) if voice else "0",
            }
            result["current_version_id"] = (
                str(row.current_version_id) if row.current_version_id is not None else None
            )
        if kind == "shot":
            result["media_state"] = {
                kind: [
                    {"id": str(item.id), "media_id": str(item.media_id)}
                    for item in storyboard.session.scalars(
                        select(model)
                        .where(model.shot_id == row.id)
                        .order_by(model.id)
                        .with_for_update()
                    )
                ]
                for kind, model in (("images", ShotImage), ("videos", ShotVideo))
            }
            result["asset_ids"] = [str(value) for value in storyboard.dao.asset_ids(row.id)]
            result["image_settings"] = result["image_settings"] or DEFAULT_IMAGE_SETTINGS.copy()
            dialogue = storyboard.session.get(ShotDialogue, row.id, with_for_update=True)
            result["dialogue"] = {
                "row_version": str(dialogue.row_version) if dialogue else "0",
                "document": deepcopy(dialogue.document)
                if dialogue
                else {"lines": [], "reviewed": False},
            }
        return result
