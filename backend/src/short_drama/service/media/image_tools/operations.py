"""可重放的私人图片处理记录；共享结果通过图片组发布。"""

import hashlib
import json
import logging
from io import BytesIO

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.dao.base import BaseDAO
from short_drama.domain import ImageGroup, ImageGroupItem, ImageOperation, MediaFile
from short_drama.schemas.media.image_groups.groups import ImageOperationCreate
from short_drama.service.base import utcnow
from short_drama.service.creation.assets.images.image import inspect_image_upload
from short_drama.service.creation.writing.script.publication import publish
from short_drama.service.media.files.storage import StorageService
from short_drama.service.media.image_groups.groups import ImageGroupService
from short_drama.service.media.processing.image import (
    composite_emotion,
    decode,
    emotion_artifacts,
    process,
)

AI_OPERATIONS = {"mask", "emotion", "texture", "angle", "lighting", "reverse_prompt"}
logger = logging.getLogger(__name__)


class ImageOperationService(ImageGroupService):
    model = ImageOperation

    def _source(self, item_id, *, lock=True, allow_archived=False):
        item = self._require(ImageGroupItem, item_id, for_update=False)
        group = self._require(ImageGroup, item.group_id, for_update=False)
        kind = "asset" if group.asset_id else "shot"
        row, scope = self.target(
            kind, group.asset_id or group.shot_id, lock=lock, allow_archived=allow_archived
        )
        if lock and not (group.shot_id and row.deleted_at is not None):
            self.sync_current(kind, row, scope)
        group = self._require(ImageGroup, group.id, for_update=lock)
        item = self._require(ImageGroupItem, item.id, for_update=lock)
        return row, scope, group, item

    def _operation(self, identifier, *, lock=True, allow_archived=False):
        operation = self._require(ImageOperation, identifier, for_update=False)
        values = self._source(operation.item_id, lock=lock, allow_archived=allow_archived)
        operation = self._require(ImageOperation, identifier, for_update=lock)
        return operation, *values

    def read_operation(self, operation):
        return {
            "id": str(operation.id),
            "item_id": str(operation.item_id),
            "operation_type": operation.operation_type,
            "status": operation.status,
            "task_id": str(operation.task_id) if operation.task_id else None,
            "result_media_id": str(operation.result_media_id)
            if operation.result_media_id
            else None,
            "result_group_id": str(operation.result_group_id)
            if operation.result_group_id
            else None,
            "mask_media_id": str(operation.mask_media_id) if operation.mask_media_id else None,
        }

    def create_operation(self, payload, key):
        try:
            return self._create_operation(payload, key)
        except Conflict:
            # 并发请求可能在首次查重后提交了同一个编号；复查持久回执。
            parsed = ImageOperationCreate.model_validate(payload)
            digest = self._digest(parsed)
            actor = require_actor(self.session)
            with self._transaction(read_only=True):
                existing = self.session.scalar(
                    select(ImageOperation).where(
                        ImageOperation.created_by == actor.user_id,
                        ImageOperation.request_key == key,
                    )
                )
                if existing and existing.request_hash == digest:
                    return self.read_operation(existing)
            raise

    @staticmethod
    def _digest(parsed):
        values = parsed.model_dump(mode="json")
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def _create_operation(self, payload, key):
        parsed = ImageOperationCreate.model_validate(payload)
        actor = require_actor(self.session)
        from short_drama.service.generations.admission.task_policy import validate_idempotency_key

        key = validate_idempotency_key(key)
        digest = self._digest(parsed)
        with self._transaction():
            existing = self.session.scalar(
                select(ImageOperation).where(
                    ImageOperation.created_by == actor.user_id, ImageOperation.request_key == key
                )
            )
            if existing:
                if existing.request_hash != digest:
                    raise Conflict("此处理请求编号已用于不同操作")
                return self.read_operation(existing)
            _row, scope, group, item = self._source(parsed.item_id)
            if item.media_id is None or item.item_version != parsed.expected_item_version:
                raise WorkflowError(
                    "image_version_conflict", "图片已变化，请刷新后重新打开工具", 409
                )
            if group.active_slot != 1:
                raise WorkflowError(
                    "image_group_changed", "当前图片组已变化，请在当前作品中使用工具", 409
                )
            if parsed.restore_media_id and str(parsed.restore_media_id) not in (
                item.origin or {}
            ).get("history", []):
                raise NotFound("原图不属于此图片位置的历史")
            operation = BaseDAO(self.session, ImageOperation).create(
                {
                    "scope_user_id": scope[0],
                    "project_id": scope[1],
                    "item_id": item.id,
                    "source_media_id": item.media_id,
                    "source_version": item.item_version,
                    "operation_type": parsed.operation_type,
                    "parameters": {
                        **parsed.parameters.model_dump(),
                        "restore_media_id": str(parsed.restore_media_id)
                        if parsed.restore_media_id
                        else None,
                    },
                    "prepared_data": {
                        "source_hash": self.context("asset" if group.asset_id else "shot", _row)
                    },
                    "request_key": key,
                    "request_hash": digest,
                    "status": "pending",
                    "created_by": actor.user_id,
                    "created_at": utcnow(),
                }
            )
            return self.read_operation(operation)

    def get_operation(self, identifier):
        with self._transaction(read_only=True):
            return self.read_operation(self._require(ImageOperation, identifier, for_update=False))

    def _bytes(self, locator):
        with StorageService(self.storage, self.settings).open(locator) as stream:
            chunks, length = [], 0
            for chunk in stream.stream(64 * 1024):
                length += len(chunk)
                if length > 50 * 1024**2:
                    raise WorkflowError("image_too_large", "源图片超过 50 MiB，无法处理", 422)
                chunks.append(chunk)
        return b"".join(chunks)

    def _upload(self, data):
        inspected = inspect_image_upload(BytesIO(data))
        try:
            stored = StorageService(self.storage, self.settings).upload(
                inspected.stream, length=inspected.byte_size, content_type=inspected.content_type
            )
            return {
                "format_code": inspected.content_type,
                "storage_locator": stored.storage_locator,
                "original_name": "图片处理结果",
                "byte_size": inspected.byte_size,
                "width": inspected.width,
                "height": inspected.height,
                "checksum_sha256": inspected.checksum_sha256,
            }
        finally:
            inspected.stream.close()

    def execute_operation(self, identifier, upload=None):
        """本地像素处理和存储 I/O 在事务外；提交时再次检查图片和当前组。"""
        uploaded = None
        if upload is not None:
            inspected = inspect_image_upload(upload)
            try:
                uploaded = inspected.stream.read()
            finally:
                inspected.stream.close()
        with self._transaction():
            operation, row, _scope, group, item = self._operation(identifier)
            if operation.status in {"completed", "conflict", "prepared", "running"}:
                if uploaded and operation.upload_checksum != hashlib.sha256(uploaded).hexdigest():
                    raise Conflict("原请求已处理其他文件，请创建新的替换操作")
                return self.read_operation(operation)
            self._check_source(operation, group, item, row)
            media = self._validate_media(operation.source_media_id, "image", for_update=False)
            locator, kind, parameters = (
                media.storage_locator,
                operation.operation_type,
                dict(operation.parameters),
            )
            restore_id = parameters.get("restore_media_id")
        created, prepared = [], {}
        try:
            source = self._bytes(locator)
            if kind == "emotion":
                artifacts, prepared = emotion_artifacts(source, parameters)
                for data in artifacts:
                    created.append(self._upload(data))
            elif kind == "mask":
                if uploaded is None:
                    raise WorkflowError("mask_required", "请先绘制需要重绘的区域", 422)
                from PIL import Image

                with Image.open(BytesIO(uploaded)) as mask:
                    if (
                        mask.format != "PNG"
                        or "A" not in mask.getbands()
                        or mask.size != decode(source).size
                        or mask.getchannel("A").getextrema()[0] != 0
                    ):
                        raise WorkflowError(
                            "invalid_mask_image", "蒙版须为与源图同尺寸且带透明编辑区域的 PNG", 422
                        )
                created = [self._upload(uploaded)]
            elif kind in {"replace", "annotation"}:
                if not restore_id:
                    if uploaded is None:
                        raise WorkflowError("image_required", "请选择替换图片或保存标注结果", 422)
                    if kind == "annotation" and decode(uploaded).size != decode(source).size:
                        raise WorkflowError(
                            "invalid_annotation_size", "标注结果须与原图尺寸一致", 422
                        )
                    created = [self._upload(uploaded)]
            elif kind not in AI_OPERATIONS:
                for data in process(source, kind, parameters):
                    created.append(self._upload(data))
            with self._transaction():
                operation, row, scope, group, item = self._operation(identifier)
                if operation.status in {"completed", "conflict", "prepared", "running"}:
                    if (
                        uploaded
                        and operation.upload_checksum != hashlib.sha256(uploaded).hexdigest()
                    ):
                        raise Conflict("原请求已处理其他文件，请创建新的替换操作")
                    return self.read_operation(operation)
                self._check_source(operation, group, item, row)
                media_rows = [
                    self._media(values, scope, operation.created_by) for values in created
                ]
                operation.upload_checksum = (
                    hashlib.sha256(uploaded).hexdigest() if uploaded else None
                )
                if kind in AI_OPERATIONS:
                    if kind == "emotion":
                        prepared["reference_media_ids"] = [
                            str(media_rows[0].id),
                            str(media_rows[1].id),
                        ]
                        operation.mask_media_id = media_rows[2].id
                    elif kind == "mask":
                        operation.mask_media_id = media_rows[0].id
                    operation.prepared_data = {**operation.prepared_data, **prepared}
                    operation.status = "prepared"
                elif kind == "split":
                    result = self.new_group(
                        "asset" if group.asset_id else "shot",
                        row,
                        scope,
                        len(media_rows),
                        "split",
                        origin={
                            key: value for key, value in item.origin.items() if key != "history"
                        },
                        source_context=dict(group.source_context),
                    )
                    for position, media in zip(
                        self.groups.items(result.id), media_rows, strict=True
                    ):
                        position.media_id = media.id
                        publish(media)
                    # 新切分组来自当前图；不以旧生成时的主图作自动写入前置条件。
                    self.write_primary(result, self.groups.items(result.id)[0], manual=True)
                    result.status, operation.status = "completed", "completed"
                    operation.result_group_id = result.id
                else:
                    media = (
                        self._validate_media(restore_id, "image") if restore_id else media_rows[0]
                    )
                    self.replace_locked(operation, row, scope, group, item, media)
                return self.read_operation(operation)
        finally:
            # 尚未入库的对象仅属于本次操作；并发重放产生的临时对象可安全清理。
            locators = [values["storage_locator"] for values in created]
            if locators:
                with self._transaction(read_only=True):
                    used = set(
                        self.session.scalars(
                            select(MediaFile.storage_locator).where(
                                MediaFile.storage_locator.in_(locators)
                            )
                        )
                    )
                for locator in set(locators) - used:
                    try:
                        StorageService(self.storage, self.settings).delete(locator)
                    except Exception:
                        logger.exception(
                            "清理图片处理临时对象失败：operation=%s locator=%s", identifier, locator
                        )

    def _media(self, values, scope, creator):
        return BaseDAO(self.session, MediaFile).create(
            {
                **values,
                "scope_user_id": scope[0],
                "project_id": scope[1],
                "created_by": creator,
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )

    def _check_source(self, operation, group, item, row):
        if (
            group.active_slot != 1
            or item.item_version != operation.source_version
            or item.media_id != operation.source_media_id
            or operation.prepared_data.get("source_hash")
            != self.context("asset" if group.asset_id else "shot", row)
        ):
            raise WorkflowError(
                "image_version_conflict", "图片或当前图片组已变化，原图保持不变，请刷新后重试", 409
            )

    def replace_locked(self, operation, row, scope, group, item, media):
        publish(media)
        if (
            (group.shot_id is not None and row.deleted_at is not None)
            or group.active_slot != 1
            or item.item_version != operation.source_version
            or item.media_id != operation.source_media_id
            or operation.prepared_data.get("source_hash")
            != self.context("asset" if group.asset_id else "shot", row)
        ):
            historical = self.new_group(
                "asset" if group.asset_id else "shot",
                row,
                scope,
                1,
                "edit",
                activate=False,
                origin=dict(item.origin),
                source_context=dict(group.source_context),
            )
            position = self.groups.items(historical.id)[0]
            position.media_id = media.id
            historical.primary_index, historical.primary_mode, historical.status = (
                1,
                "manual",
                "completed",
            )
            historical.notice = "处理期间原图已变化，此结果已保留在历史中。"
            operation.result_group_id, operation.status = historical.id, "conflict"
        else:
            origin = dict(item.origin)
            origin["history"] = list(
                dict.fromkeys([*(origin.get("history") or []), str(item.media_id)])
            )
            origin["prompt_media_id"] = origin.get("prompt_media_id") or str(item.media_id)
            was_primary = group.primary_index == item.output_index
            item.origin, item.media_id, item.item_version = origin, media.id, item.item_version + 1
            if was_primary:
                # 更新主图本身；保留用户选中的图位，不影响下次生成参数。
                mode = group.primary_mode
                self.write_primary(group, item, manual=True)
                group.primary_mode = mode
            operation.status = "completed"
        operation.result_media_id = media.id
        self.session.flush()

    def prepare_generation(self, kind, payload):
        source = payload["source"]
        operation, _row, scope, group, item = self._operation(source["operation_id"])
        expected = "text" if operation.operation_type == "reverse_prompt" else "image"
        if operation.operation_type not in AI_OPERATIONS or kind != expected:
            raise WorkflowError("invalid_image_operation", "处理类型与生成服务不匹配", 422)
        if operation.status != "prepared" or operation.task_id is not None:
            raise WorkflowError(
                "image_operation_not_ready", "请先准备图片处理；已提交的操作请核对原任务", 409
            )
        self._check_source(operation, group, item, _row)
        if scope[1]:
            source["project_id"] = str(scope[1])
        parameters = operation.parameters
        if kind == "text":
            prompt = (
                "请详细描述参考图片中的主体、场景、构图、镜头、光线、色彩和风格，"
                "输出可用于重新生成此画面的中文提示词。只输出提示词。"
            )
            if parameters["instructions"].strip():
                prompt += "\n补充要求：" + parameters["instructions"].strip()
            payload["input"] = {
                "messages": [{"role": "user", "content": prompt}],
                "reference_media_ids": [str(operation.source_media_id)],
            }
        else:
            if payload["parameters"].get("count", 1) != 1:
                raise WorkflowError("invalid_image_count", "单图处理每次生成一张图片", 422)
            prompt = parameters["instructions"].strip()
            if not prompt:
                raise WorkflowError("image_instructions_required", "请填写图片处理要求", 422)
            payload["input"] = {
                "prompt": prompt,
                "reference_media_ids": operation.prepared_data.get("reference_media_ids")
                or [str(operation.source_media_id)],
            }
            payload["parameters"] = {"count": 1}
            payload["image_edit_parameters"] = {"mode": "image", "size": "auto"}
        payload["source_snapshot"] = {
            "image_group_id": str(group.id),
            "item_id": str(item.id),
            "source_media_id": str(operation.source_media_id),
            "source_version": str(operation.source_version),
            "operation_type": operation.operation_type,
            "parameters": parameters,
            "prepared_data": operation.prepared_data,
        }
        return payload

    def attach(self, task, record, media_id):
        identifier = (record.request_data.get("source") or {}).get("operation_id")
        if not identifier:
            return
        operation, row, scope, group, item = self._operation(identifier, allow_archived=True)
        if operation.task_id != task.id:
            raise Conflict("图片处理任务不匹配")
        if operation.result_media_id:
            if operation.result_media_id != media_id:
                raise Conflict("图片处理结果不匹配")
            return
        self.replace_locked(
            operation, row, scope, group, item, self._validate_media(media_id, "image")
        )

    def composite(self, record, data):
        snapshot = record.request_data.get("source_snapshot") or {}
        if snapshot.get("operation_type") != "emotion":
            return data
        with self._transaction(read_only=True):
            source = self._validate_media(snapshot["source_media_id"], "image", for_update=False)
            locator = source.storage_locator
        return composite_emotion(self._bytes(locator), data, snapshot["prepared_data"])
