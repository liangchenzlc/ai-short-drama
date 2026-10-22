"""图片组归档与主图更新；可在 Worker 的既有事务中幂等执行。"""

import json

from sqlalchemy import func, or_, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.image_group_dao import ImageGroupDAO
from short_drama.db.access import require_project, scope_of, set_scope
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AsyncTask,
    ImageGroup,
    ImageGroupItem,
    MediaAsset,
    MediaFile,
    Project,
    ShotImage,
    ShotScript,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.image_group import ImagePrimarySet

from .asset_image_context import asset_image_content, asset_image_content_hash
from .base import BaseService, utcnow
from .publication import publish
from .storage_service import StorageService


class ImageGroupService(BaseService):
    model = ImageGroup

    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.settings, self.storage = settings, storage
        self.groups = ImageGroupDAO(session)

    def target(self, kind, identifier, *, lock=True, allow_archived=False):
        if kind not in {"asset", "shot"}:
            raise NotFound("图片所属内容不存在")
        initial = self._require(
            Asset if kind == "asset" else ShotScript, identifier, for_update=False
        )
        initial_scope = scope_of(self.session, initial)
        if initial_scope[1]:
            require_project(self.session, initial_scope[1], lock=lock)
        if kind == "shot" and lock:
            project_id = self.session.scalar(
                select(ShotScript.project_id).where(ShotScript.id == parse_identifier(identifier))
            )
            if project_id is None:
                raise NotFound("镜头不存在")
            self._require(Project, project_id)
        row = self._require(Asset if kind == "asset" else ShotScript, identifier, for_update=lock)
        if row.deleted_at is not None and not allow_archived:
            raise NotFound("素材已删除" if kind == "asset" else "镜头已归档")
        scope = scope_of(self.session, row)
        set_scope(self.session, scope)
        return row, scope

    def context(self, kind, row):
        if kind == "asset":
            return asset_image_content_hash(asset_image_content(row))
        from .generation_context_service import GenerationContextService

        return GenerationContextService(self.session).locked_shot_context(row.id)[4]

    def current_media(self, kind, row):
        if kind == "asset":
            return row.media_id
        current = self.session.scalar(
            select(ShotImage).where(ShotImage.shot_id == row.id).with_for_update()
        )
        return current.media_id if current else None

    def new_group(
        self,
        kind,
        row,
        scope,
        count,
        group_kind,
        *,
        task_id=None,
        origin=None,
        activate=True,
        source_context=None,
    ):
        previous = (
            list(self.groups.for_target(kind, row.id, current=True, lock=True)) if activate else []
        )
        for old in previous:
            old.active_slot = None
        self.session.flush()
        group = self.groups.create(
            {
                "asset_id": row.id if kind == "asset" else None,
                "shot_id": row.id if kind == "shot" else None,
                "scope_user_id": scope[0],
                "project_id": scope[1],
                "active_slot": 1 if activate else None,
                "kind": group_kind,
                "status": "generating",
                "expected_count": count,
                "task_id": task_id,
                "primary_index": None,
                "primary_mode": "pending",
                "primary_version": 1,
                "source_context": source_context
                or {
                    "hash": self.context(kind, row),
                    "media_id": str(self.current_media(kind, row))
                    if self.current_media(kind, row)
                    else None,
                },
                "created_by": getattr(self.session.info.get("actor"), "user_id", None),
                "created_at": utcnow(),
            }
        )
        for index in range(1, count + 1):
            BaseDAO(self.session, ImageGroupItem).create(
                {
                    "group_id": group.id,
                    "output_index": index,
                    "media_id": None,
                    "item_version": 1,
                    "origin": origin or {},
                    "created_at": utcnow(),
                }
            )
        return group

    def admit(self, task, record, *, assistant_managed=False):
        request = dict(record.request_data)
        source = request.get("source") or {}
        if task.service_type != "image" or source.get("scene") not in {"asset_image", "shot_image"}:
            return
        kind = "asset" if source["scene"] == "asset_image" else "shot"
        row, scope = self.target(kind, source[f"{kind}_id"])
        self.sync_current(kind, row, scope)
        parameters = request.get("parameters") or {}
        origin = {
            "layout": source.get("layout", "single"),
            "aspect": parameters.get("aspect"),
            "resolution": parameters.get("resolution") or "2K",
            "context_hash": self.context(kind, row),
        }
        group = self.new_group(
            kind,
            row,
            scope,
            parameters.get("count", 1),
            "generation",
            task_id=task.id,
            origin=origin,
            activate=assistant_managed or not record.config_snapshot.get("agent_managed", False),
            source_context={
                "hash": self.context(kind, row),
                "media_id": str(self.current_media(kind, row))
                if self.current_media(kind, row)
                else None,
                "target_version": str(row.row_version),
            }
            if assistant_managed
            else None,
        )
        group.created_by = task.initiated_by
        request["image_group_id"] = str(group.id)
        record.request_data = request
        self.session.flush()

    def write_primary(self, group, item, *, manual=False):
        kind = "asset" if group.asset_id else "shot"
        row, _scope = self.target(kind, group.asset_id or group.shot_id, allow_archived=not manual)
        if row.deleted_at is not None:
            label = "素材已删除" if kind == "asset" else "分镜已归档"
            group.notice = f"{label}，生成图片已保存，未更新主图。"
            return False
        if item.media_id is None:
            raise WorkflowError("image_not_ready", "图片尚未保存完成", 409)
        media = self._validate_media(item.media_id, "image")
        current_hash = self.context(kind, row)
        if not manual and group.source_context.get("target_version") is not None:
            project = self.session.get(Project, row.project_id)
            if project.archived_at or project.owner_user_id != group.created_by:
                group.notice = "作品已归档或权限已变化，结果已保存，未更新主图。"
                return False
        if (
            not manual
            and group.source_context.get("target_version") is not None
            and (
                group.primary_version == UINT64_MAX
                or row.row_version == UINT64_MAX
                or kind == "shot"
                and self.session.get(Project, row.project_id).storyboard_version == UINT64_MAX
            )
        ):
            group.notice = "图片或作品版本已达到上限，结果已保存，未更新主图。"
            return False
        if not manual and (
            group.active_slot != 1
            or group.primary_mode != "pending"
            or current_hash != group.source_context.get("hash")
            or str(self.current_media(kind, row) or "")
            != str(group.source_context.get("media_id") or "")
            or group.source_context.get("target_version") is not None
            and str(row.row_version) != group.source_context["target_version"]
        ):
            if group.active_slot == 1 and group.primary_mode == "pending":
                group.notice = "创作内容或当前主图已变化，结果已保存，请核对后设置主图。"
            return False
        if manual and group.active_slot != 1:
            for previous in self.groups.for_target(kind, row.id, current=True, lock=True):
                previous.active_slot = None
            self.session.flush()
            group.active_slot = 1
        storyboard_before = (
            str(self.session.get(Project, row.project_id).storyboard_version)
            if kind == "shot"
            and not manual
            and group.source_context.get("target_version") is not None
            else None
        )
        publish(media)
        if (
            self.current_media(kind, row) != media.id
            or manual
            and (kind == "asset" and row.state != "confirmed" or kind == "shot")
        ):
            if kind == "asset":
                from .asset_version_service import AssetVersionService

                row.media_id, row.model_id, row.state = media.id, None, "confirmed"
                row.row_version += 1
                row.updated_at = utcnow()
                self.session.flush()
                AssetVersionService(self.session).append_locked(row)
            else:
                from .shot_image_service import ShotImageService

                existing = self.session.scalar(
                    select(ShotImage).where(ShotImage.shot_id == row.id).with_for_update()
                )
                metadata = item.origin or {}
                values = {
                    "project_id": row.project_id,
                    "shot_id": row.id,
                    "media_id": media.id,
                    "model_id": None,
                    "prompt": "",
                    "layout": metadata.get("layout", "single"),
                    "aspect": metadata.get("aspect"),
                    "resolution": metadata.get("resolution", "2K"),
                    "context_hash": current_hash
                    if manual
                    else metadata.get("context_hash") or current_hash,
                }
                ShotImageService(self.session)._write_confirmed(values, row, existing=existing)
        group.primary_index = item.output_index
        group.primary_mode = "manual" if manual else "auto"
        group.primary_version += 1
        group.notice = None
        self.session.flush()
        if not manual and group.source_context.get("target_version") is not None:
            from .image_adoption_state import image_adoption_state

            group.source_context = {
                **group.source_context,
                "assistant_adoption_snapshot": image_adoption_state(self.session, row, kind),
                **(
                    {
                        "assistant_storyboard_transition": {
                            "before": storyboard_before,
                            "after": str(
                                self.session.get(Project, row.project_id).storyboard_version
                            ),
                        }
                    }
                    if storyboard_before is not None
                    else {}
                ),
            }
        return True

    def attach(self, task, record, media_id, index):
        identifier = record.request_data.get("image_group_id")
        if not identifier:
            return
        initial = self._require(ImageGroup, identifier, for_update=False)
        self.target(
            "asset" if initial.asset_id else "shot",
            initial.asset_id or initial.shot_id,
            allow_archived=True,
        )
        group = self._require(ImageGroup, identifier)
        items = self.groups.items(group.id, lock=True)
        item = next((entry for entry in items if entry.output_index == index), None)
        if item is None:
            return
        if item.media_id is None:
            item.media_id = media_id
            item.item_version += 1
            publish(self._validate_media(media_id, "image"))
            self.session.flush()
        elif item.media_id != media_id:
            raise WorkflowError("image_output_conflict", "图片位置已有不同的结果", 409)
        if index == 1:
            self.write_primary(group, item)

    def task_state(self, task):
        if task.status in {"queued", "running"}:
            group = self.session.scalar(
                select(ImageGroup).where(ImageGroup.task_id == task.id).with_for_update()
            )
            if group:
                group.status = "generating"
            from short_drama.domain import ImageOperation

            operation = self.session.scalar(
                select(ImageOperation).where(ImageOperation.task_id == task.id).with_for_update()
            )
            if operation and operation.status == "failed":
                operation.status = "running"
        else:
            self.finalize(task.id)

    def finalize(self, task_id):
        group = self.session.scalar(select(ImageGroup).where(ImageGroup.task_id == task_id))
        if group is None:
            from short_drama.domain import ImageOperation

            operation = self.session.scalar(
                select(ImageOperation).where(ImageOperation.task_id == task_id)
            )
            task = self.session.get(AsyncTask, task_id)
            if operation and task and operation.status not in {"completed", "conflict"}:
                operation.status = "completed" if task.status == "succeeded" else "failed"
            return
        self.target(
            "asset" if group.asset_id else "shot",
            group.asset_id or group.shot_id,
            allow_archived=True,
        )
        group = self._require(ImageGroup, group.id)
        items = self.groups.items(group.id, lock=True)
        if group.source_context.get("target_version") is not None:
            record = self.session.scalar(
                select(AIGenerationRecord)
                .where(
                    AIGenerationRecord.task_id == task_id,
                )
                .order_by(AIGenerationRecord.call_no.desc())
                .limit(1)
            )
            if record is not None and record.status in {"sent", "unknown"}:
                group.status = "generating"
                return
        ready = [item for item in items if item.media_id]
        group.status = "completed" if len(ready) == len(items) else "partial" if ready else "failed"
        if ready and group.primary_mode == "pending":
            if self.write_primary(group, ready[0]) and ready[0].output_index != 1:
                group.notice = f"第 1 张未成功，已将第 {ready[0].output_index} 张设为主图。"

    def _legacy(self, kind, row, scope):
        media_id = self.current_media(kind, row)
        if media_id is None:
            return None
        group = self.new_group(kind, row, scope, 1, "legacy")
        item = self.groups.items(group.id, lock=True)[0]
        item.media_id = media_id
        if kind == "shot":
            image = self.session.scalar(select(ShotImage).where(ShotImage.shot_id == row.id))
            item.origin = {
                "layout": image.layout,
                "aspect": image.aspect,
                "resolution": image.resolution,
                "context_hash": image.context_hash,
            }
        group.primary_index, group.primary_mode = 1, "manual"
        group.status = "completed"
        self.session.flush()
        return group

    def sync_current(self, kind, row, scope):
        """兼容上传、媒体库和回收恢复入口，保留它们替换前后的整组历史。"""
        group = self.groups.for_target(kind, row.id, current=True, lock=True).first()
        current_media_id = self.current_media(kind, row)
        if group is not None:
            items = self.groups.items(group.id)
            primary = next(
                (item for item in items if item.output_index == group.primary_index), None
            )
            expected = primary.media_id if primary else group.source_context.get("media_id")
            if str(current_media_id or "") == str(expected or ""):
                return group
        if current_media_id is not None:
            return self._legacy(kind, row, scope)
        if group is not None:
            group.active_slot = None
            self.session.flush()
        return None

    def read_group(self, group):
        task = self.session.get(AsyncTask, group.task_id) if group.task_id else None
        # Scoped task queries retain the initiating account's execution boundary.
        terminal = group.status in {"completed", "partial", "failed"} or (
            task is not None and task.status in {"succeeded", "failed", "cancelled"}
        )
        actor = self.session.info.get("actor")
        record = (
            self.session.scalar(
                select(AIGenerationRecord)
                .where(AIGenerationRecord.task_id == group.task_id)
                .order_by(AIGenerationRecord.call_no.desc())
                .limit(1)
            )
            if task
            else None
        )
        manifest = (record.response_data or {}).get("media_manifest", []) if record else []
        result = []
        for item in self.groups.items(group.id):
            media = self.session.get(MediaFile, item.media_id) if item.media_id else None
            entry = next((e for e in manifest if e["output_index"] == item.output_index), {})
            status = "ready" if media else "failed" if terminal else "generating"
            result.append(
                {
                    "id": str(item.id),
                    "output_index": item.output_index,
                    "media_id": str(media.id) if media else None,
                    "url": StorageService(self.storage, self.settings).download_url(
                        media.storage_locator
                    )
                    if media and self.storage
                    else None,
                    "width": media.width if media else None,
                    "height": media.height if media else None,
                    "item_version": str(item.item_version),
                    "status": status,
                    "error": (entry.get("save_error") or {}).get("message") if not media else None,
                    "history": [
                        self.media_read(identifier)
                        for identifier in (item.origin or {}).get("history", [])
                    ],
                    "prompt": self.private_prompt(item),
                }
            )
        return {
            "id": str(group.id),
            "kind": group.kind,
            "is_current": group.active_slot == 1,
            "target_kind": "asset" if group.asset_id else "shot",
            "target_id": str(group.asset_id or group.shot_id),
            "expected_count": group.expected_count,
            "primary_index": group.primary_index,
            "primary_version": str(group.primary_version),
            "notice": group.notice,
            "created_at": group.created_at,
            "status": group.status,
            "target_version": str(
                self._require(
                    Asset if group.asset_id else ShotScript,
                    group.asset_id or group.shot_id,
                    for_update=False,
                ).row_version
            ),
            "task_id": str(group.task_id)
            if group.task_id and (not actor or group.created_by == actor.user_id)
            else None,
            "items": result,
        }

    def media_read(self, identifier):
        media = self._validate_media(identifier, "image", for_update=False)
        return {
            "media_id": str(media.id),
            "width": media.width,
            "height": media.height,
            "url": StorageService(self.storage, self.settings).download_url(media.storage_locator)
            if self.storage
            else None,
        }

    def private_prompt(self, item):
        identifier = (item.origin or {}).get("prompt_media_id") or item.media_id
        if not identifier:
            return None
        # 关联的生成记录与私人提示词仅向发起账号提供。
        record = self.session.scalar(
            select(AIGenerationRecord)
            .join(MediaAsset, MediaAsset.record_id == AIGenerationRecord.id)
            .where(MediaAsset.media_id == int(identifier))
            .limit(1)
        )
        return (record.request_data.get("input") or {}).get("prompt") if record else None

    def current(self, kind, identifier):
        with self._transaction():
            row, scope = self.target(kind, identifier)
            group = self.sync_current(kind, row, scope)
            return self.read_group(group) if group else None

    def history(self, kind, identifier, offset=0, limit=20):
        with self._transaction(read_only=True):
            row, _scope = self.target(kind, identifier, lock=False)
            field = ImageGroup.asset_id if kind == "asset" else ImageGroup.shot_id
            groups = self.session.scalars(
                select(ImageGroup)
                .where(field == row.id)
                .order_by(ImageGroup.created_at.desc(), ImageGroup.id.desc())
                .offset(offset)
                .limit(limit)
            )
            total = self.session.scalar(
                select(func.count()).select_from(ImageGroup).where(field == row.id)
            )
            return {
                "items": [self.read_group(g) for g in groups],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def detail(self, identifier):
        with self._transaction(read_only=True):
            return self.read_group(self._require(ImageGroup, identifier, for_update=False))

    def primary(self, identifier, payload):
        parsed = ImagePrimarySet.model_validate(payload)
        with self._transaction():
            group = self.primary_locked(identifier, parsed)
            return self.read_group(group)

    def primary_locked(self, identifier, payload):
        """Caller owns the transaction; avoid the public DTO's private prompt/URL projection."""
        if not self.session.in_transaction():
            raise WorkflowError("transaction_required", "主图写入需要已有事务", 500)
        parsed = ImagePrimarySet.model_validate(self._payload(ImagePrimarySet, payload))
        group = self._require(ImageGroup, identifier, for_update=False)
        kind = "asset" if group.asset_id else "shot"
        row, _scope = self.target(kind, group.asset_id or group.shot_id)
        group = self._require(ImageGroup, identifier)
        item = self._require(ImageGroupItem, parsed.item_id)
        if item.group_id != group.id:
            raise NotFound("图片不属于该组")
        if (
            group.primary_version != parsed.expected_primary_version
            or item.item_version != parsed.expected_item_version
            or row.row_version != parsed.expected_target_version
        ):
            raise WorkflowError("image_version_conflict", "主图或图片已变化，请刷新核对", 409)
        self.write_primary(group, item, manual=True)
        return group

    def source(self, identifier):
        with self._transaction(read_only=True):
            media = self._validate_media(parse_identifier(identifier), "image", for_update=False)
            item = self.session.scalar(
                select(ImageGroupItem)
                .where(
                    or_(
                        ImageGroupItem.media_id == media.id,
                        func.json_contains(
                            ImageGroupItem.origin, json.dumps(str(media.id)), "$.history"
                        )
                        == 1,
                    )
                )
                .limit(1)
            )
            if item is None:
                raise NotFound("图片不属于可访问的图片组")
            return media.storage_locator, media.format_code
