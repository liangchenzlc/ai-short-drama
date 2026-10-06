"""画布任务准入、本人任务投影及恢复；复用现有异步生成执行器。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from sqlalchemy.orm import Session

from short_drama.ai import GenerationError, select_adapter
from short_drama.core.exceptions import Conflict, GenerationRequestError, NotFound, WorkflowError
from short_drama.dao.canvas_generation_dao import CanvasGenerationDAO
from short_drama.dao.canvas_model_test_dao import CanvasModelTestDAO
from short_drama.dao.canvas_text_dao import CanvasTextDAO
from short_drama.domain import AsyncTask, CanvasTaskBinding, ProjectCanvas
from short_drama.schemas.canvas import CanvasDocument
from short_drama.schemas.canvas_generation import CanvasTaskRegistration
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate

from .ai_generation_service import AIGenerationService, can_retry, resume_action, safe_error
from .base import BaseService
from .canvas_credential_freeze import freeze_canvas_credentials
from .canvas_document import content_hash
from .canvas_generation_inputs import (
    frozen_canvas_parameters,
    generation_payload,
    normalize_video_request,
)
from .canvas_model_test_service import CanvasModelTestService
from .canvas_service import CanvasService, iso
from .canvas_task_service import CanvasTaskService
from .canvas_task_state import canvas_task_stage
from .canvas_text_admission import freeze_text_references
from .canvas_video_admission import VIDEO_ADAPTERS, freeze_video_references


class CanvasGenerationService(BaseService):
    model = CanvasTaskBinding

    def __init__(self, session: Session, settings) -> None:
        super().__init__(session)
        self.canvases = CanvasService(session)
        self.generation = AIGenerationService(session, settings)
        self.tasks = CanvasTaskService(session)
        self.runtime = CanvasGenerationDAO(session)
        self.model_tests = CanvasModelTestService(session, settings)

    def _canvas(self, source_key: str) -> ProjectCanvas:
        canvas = self.runtime.canvas(source_key)
        if canvas is None:
            raise NotFound("活动画布不存在或无权访问")
        return self.canvases.require_canvas(canvas.project_id, canvas.id, lock=True)

    def _owned(self, identifier: int) -> tuple[ProjectCanvas, AsyncTask, CanvasTaskBinding]:
        binding = self.runtime.binding(identifier, self.canvases.actor_id)
        if binding is None:
            raise NotFound("本人画布任务不存在")
        canvas = self.canvases.require_canvas(binding.project_id, binding.canvas_id, lock=True)
        task = self.tasks.tasks.task(identifier)
        if task is None or task.initiated_by != self.canvases.actor_id:
            raise NotFound("本人画布任务不存在")
        return canvas, task, binding

    def create(self, payload: CanvasRuntimeTaskCreate, key: str | None = None) -> tuple[dict, bool]:
        payload = CanvasRuntimeTaskCreate.model_validate(payload.model_dump(by_alias=True))
        metadata = payload.input.metadata
        if key is not None and key != metadata.client_operation_id:
            raise WorkflowError("canvas_operation_key_mismatch", "请求键与画布操作身份不同", 422)
        key = "canvas-task:" + content_hash(metadata.client_operation_id)
        original = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        digest = self.generation._hash(payload.input.mode, original, "canvas.create")
        with self._transaction():
            canvas = self._canvas(payload.project_id)
            existing = self.generation._existing(key, digest)
            if existing:
                canvas, task, binding = self._owned(int(existing[0]["generation_id"]))
                return self._project(canvas, task, binding), False
            document = self.canvases._document(canvas, private=True, lock=True)
            source_key = metadata.source_node_id or metadata.node_id
            source = next((node for node in document["nodes"] if node["id"] == source_key), None)
            target = next(
                (node for node in document["nodes"] if node["id"] == metadata.node_id), None
            )
            if source is None or target is None:
                raise WorkflowError(
                    "canvas_generation_source_not_saved", "请先保存生成来源、目标节点与连线", 409
                )
            old_task_id = metadata.retry_of
            if old_task_id is not None:
                old_canvas, old_task, old_binding = self._owned(old_task_id)
                records = self.runtime.records(old_task.id)
                if (
                    old_canvas.id != canvas.id
                    or old_binding.node_key != metadata.node_id
                    or not records
                    or not can_retry(old_task, records[-1])
                ):
                    raise WorkflowError("canvas_task_retry_not_allowed", "该任务不能安全重试", 409)
            from .canvas_model_catalog_service import CanvasModelCatalogService

            catalog = CanvasModelCatalogService(self.session, settings=self.generation.settings)
            catalog.catalog_dao.catalog(catalog.actor_id, lock=True)
            config = self.generation._config(payload.input.mode, payload.logical_model_id)
            if catalog.refresh_runtime_model_locked(config):
                self.session.flush()
            try:
                adapter = select_adapter(
                    {
                        "service_type": config.service_type,
                        "provider": config.provider,
                        "base_url": config.base_url,
                        "model_key": config.model_key,
                        "capability_cache": config.capability_cache,
                        "credential_identity": hashlib.sha256(
                            (config.apikey or "").encode()
                        ).hexdigest(),
                    }
                )
            except GenerationError as error:
                raise GenerationRequestError(error.code) from None
            secret = catalog.runtime_credentials_locked(config.id, adapter)
            if adapter in VIDEO_ADAPTERS:
                payload = normalize_video_request(payload, config.capability_cache)
            frozen_request = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
            request = generation_payload(payload, canvas.project_id, adapter)

            def freeze(prepared: dict) -> dict:
                prepared["canvas_request"] = deepcopy(frozen_request)
                prepared["source"] = {
                    "scene": "canvas_node",
                    "project_id": str(canvas.project_id),
                    "canvas_id": str(canvas.id),
                    "node_key": metadata.node_id,
                    "source_node_key": source_key,
                    "context_hash": content_hash(source),
                }
                prepared["source_snapshot"] = {"source_node": deepcopy(source)}
                freeze_text_references(
                    self.generation, prepared, canvas.project_id, adapter, config.capability_cache
                )
                if adapter in VIDEO_ADAPTERS:
                    capability = config.capability_cache or {}
                    freeze_video_references(
                        self.generation,
                        prepared,
                        canvas.project_id,
                        adapter,
                        channel_key=capability.get("canvas_channel_key", ""),
                        model=config.model_key,
                    )
                parameters = frozen_canvas_parameters(payload, adapter)
                if parameters is not None:
                    mask_id = parameters.get("mask_media_id")
                    if mask_id:
                        from short_drama.db.access import scope_of

                        mask = self.generation._validate_media(mask_id, "image")
                        if scope_of(self.session, mask) != (None, canvas.project_id):
                            raise NotFound("蒙版不存在或不属于当前项目")
                        if not mask.storage_locator.startswith("minio://"):
                            raise WorkflowError(
                                "canvas_reference_not_saved", "蒙版必须先保存为永久图片资源", 422
                            )
                    prepared["canvas_parameters"] = parameters
                return prepared

            summary, fresh = self.generation.create_locked(
                payload.input.mode, request, key, request_hash=digest, prepare_transform=freeze
            )
            if secret is not None:
                record = self.generation.record_dao.for_task(int(summary["generation_id"]))[0]
                freeze_canvas_credentials(record, secret, catalog.configs._key_cipher())
            task = self.tasks.tasks.task(int(summary["generation_id"]))
            if old_task_id is not None:
                task.retry_of_id = old_task_id
            binding = self.tasks.register_locked(
                CanvasTaskRegistration(
                    project_id=canvas.project_id,
                    canvas_id=canvas.id,
                    task_id=task.id,
                    node_key=metadata.node_id,
                    source_node_key=source_key,
                    client_operation_id=metadata.client_operation_id,
                )
            )
            target["metadata"] = {
                **(target.get("metadata") or {}),
                "taskId": str(task.id),
                "taskClientOperationId": metadata.client_operation_id,
                "taskStatus": task.status,
                "taskStage": "queued",
                "taskProgress": 0,
                "status": "loading",
            }
            self.canvases._apply_document(
                canvas, CanvasDocument.model_validate(document), reason="generation_submit"
            )
            return self._project(canvas, task, binding), fresh

    def detail(self, identifier: int) -> dict:
        with self._transaction():
            if self.runtime.binding(identifier, self.canvases.actor_id) is None:
                return self.model_tests.runtime_locked(identifier)
            return self._project(*self._owned(identifier))

    def list(
        self,
        *,
        project_id: str | None = None,
        active_only=False,
        limit=30,
        client_operation_id: str | None = None,
        source_node_id: str | None = None,
    ) -> list[dict]:
        with self._transaction():
            canvas_id = self._canvas(project_id).id if project_id is not None else None
            items = [
                self._project(*self._owned(identifier))
                for identifier in self.runtime.task_ids(
                    self.canvases.actor_id,
                    canvas_id,
                    active_only,
                    limit,
                    client_operation_id,
                    source_node_id,
                )
            ]
            if canvas_id is None and source_node_id is None:
                items.extend(
                    self.model_tests.runtime_locked(identifier)
                    for identifier in CanvasModelTestDAO(self.session).task_ids(
                        self.canvases.actor_id,
                        active_only=active_only,
                        limit=limit,
                        client_operation_id=client_operation_id,
                    )
                )
            return sorted(
                items, key=lambda item: (item["updatedAt"], int(item["id"])), reverse=True
            )[:limit]

    def action(self, identifier: int, action: str) -> dict:
        with self._transaction():
            if self.runtime.binding(identifier, self.canvases.actor_id) is None:
                self.model_tests.runtime_locked(identifier)
            else:
                self._owned(identifier)
        if action == "cancel":
            self.generation.cancel(identifier)
        elif action == "resume":
            self.generation.resume(identifier)
        else:
            raise Conflict("不支持的画布任务操作")
        return self.detail(identifier)

    def logs(self, identifier: int) -> list[dict]:
        with self._transaction(read_only=True):
            if self.runtime.binding(identifier, self.canvases.actor_id) is None:
                self.model_tests.runtime_locked(identifier)
                task = self._require(AsyncTask, identifier, for_update=False)
            else:
                _canvas, task, _binding = self._owned(identifier)
            records = self.runtime.records(task.id)
            return [
                {
                    "summary": f"第 {record.call_no} 次模型调用：{record.status}",
                    "level": "error" if record.status in {"failed", "unknown"} else "info",
                    "createdAt": iso(record.updated_at),
                }
                for record in records
            ]

    def text_replay(self, identifier: int, after: int = 0) -> dict:
        with self._transaction():
            canvas, task, binding = self._owned(identifier)
            if task.service_type != "text":
                raise WorkflowError("canvas_text_task_required", "只有文本任务支持增量回放", 422)
            projected = self._project(canvas, task, binding)
            deltas = CanvasTextDAO(self.session).deltas(binding.id, after)
            last = deltas[-1].sequence if deltas else after
            more = bool(CanvasTextDAO(self.session).deltas(binding.id, last))
            return {
                "deltas": [
                    {
                        "id": str(delta.id),
                        "taskId": str(task.id),
                        "sequence": delta.sequence,
                        "content": delta.content,
                        "byteCount": delta.byte_count,
                        "createdAt": iso(delta.created_at),
                        "expiresAt": iso(delta.expires_at),
                    }
                    for delta in deltas
                ],
                "textDraft": projected.get("textDraft", ""),
                "finalText": projected.get("textDraft", "") if task.status == "succeeded" else "",
                "complete": task.status in {"succeeded", "failed", "cancelled"} and not more,
                "status": task.status,
                "stage": projected["stage"],
                "progress": projected["progress"],
                "error": projected["error"],
            }

    def _project(self, canvas: ProjectCanvas, task: AsyncTask, binding: CanvasTaskBinding) -> dict:
        records = self.runtime.records(task.id)
        if not records or "canvas_request" not in records[0].request_data:
            raise NotFound("画布任务缺少已冻结的准入请求")
        request = records[0].request_data["canvas_request"]
        latest = records[-1]
        metadata = request["input"]["metadata"]
        stage = canvas_task_stage(task, latest)
        error = safe_error(task.error)
        value = {
            "id": str(task.id),
            "projectId": canvas.source_key,
            "type": request["type"],
            "operation": request["operation"],
            "status": task.status,
            "progress": 100 if task.status == "succeeded" else 0,
            "stage": stage,
            "prompt": request["prompt"],
            "provider": records[0].config_snapshot.get("provider"),
            "model": records[0].config_snapshot.get("model_key"),
            "clientOperationId": binding.client_operation_id,
            "retryOf": str(task.retry_of_id) if task.retry_of_id else None,
            "attemptGroupId": metadata.get("attemptGroupId"),
            "clientContext": {"nodeId": binding.node_key, "sourceNodeId": binding.source_node_key},
            "inputJson": json.dumps(request["input"], ensure_ascii=False, separators=(",", ":")),
            "resultState": "NOT_AVAILABLE",
            "outputs": [],
            "error": error["message"] if error else None,
            "errorCode": error["code"] if error else None,
            "providerRequestId": latest.provider_task_id,
            "canRetry": can_retry(task, latest),
            "canResume": bool(resume_action(task, latest)),
            "canCancel": task.status in {"queued", "running"} and not task.cancel_requested,
            "attempts": len(records),
            "createdAt": iso(task.created_at),
            "updatedAt": iso(task.updated_at),
            "startedAt": iso(task.started_at) if task.started_at else None,
            "completedAt": iso(task.finished_at) if task.finished_at else None,
        }
        if task.service_type == "text":
            value["textDraftSequence"] = CanvasTextDAO(self.session).task_usage(binding.id)[0]
        if task.status != "succeeded":
            if task.service_type == "text":
                value["textDraft"] = (latest.response_data or {}).get("canvas_text_draft", "")
            if latest.status == "succeeded":
                value["resultState"] = "PENDING_MATERIALIZATION"
            return value
        results = self.tasks.materialize_locked(canvas, binding, task)
        if not results:
            raise WorkflowError("output_not_ready", "成功任务尚无已归档的产物", 409)
        body = {"mode": task.service_type}
        if task.service_type == "text":
            body["text"] = results[0].content_json["content"]
            value["textDraft"] = body["text"]
        else:
            outputs = []
            for result in results:
                content = result.content_json
                outputs.append(
                    {
                        "outputIndex": result.result_index,
                        "dataUrl": content["content"],
                        "url": content["content"],
                        "storageKey": content["storageKey"],
                        "bytes": content["bytes"],
                        "mimeType": content["mimeType"],
                        **{
                            key: content[source]
                            for key, source in (
                                ("width", "naturalWidth"),
                                ("height", "naturalHeight"),
                                ("durationMs", "durationMs"),
                            )
                            if source in content
                        },
                    }
                )
                value["outputs"].append(
                    {
                        "outputIndex": result.result_index,
                        "mediaType": result.kind,
                        "materializedAssetId": content["assetId"],
                    }
                )
            if not outputs:
                raise WorkflowError("output_not_ready", "成功任务尚无已归档的产物", 409)
            body["images" if task.service_type == "image" else task.service_type] = (
                outputs if task.service_type == "image" else outputs[0]
            )
            if task.service_type in {"image", "video"}:
                value.update(previewUrl=outputs[0]["url"], previewKind=task.service_type)
        value["resultState"] = "READY"
        value["resultJson"] = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        return value
