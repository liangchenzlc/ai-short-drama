"""将私人租约、阶段批准和画布异步准入放进同一受控事务。"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from short_drama.ai import GenerationError, select_adapter
from short_drama.ai.canvas_tool_adapters import CANVAS_TOOL_ADAPTERS
from short_drama.ai.model_identity import model_credential_identity
from short_drama.core.config import Settings
from short_drama.core.exceptions import GenerationRequestError, NotFound, WorkflowError
from short_drama.dao.canvas_tool_run_dao import CanvasToolRunDAO
from short_drama.db.access import scope_of
from short_drama.db.canvas_tool_run_access import tool_run_write
from short_drama.domain import (
    AIModelConfig,
    CanvasToolRun,
    CanvasToolSubmission,
    MediaFile,
    ProjectCanvas,
)
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.schemas.canvas_tool_contracts import builtin_tool
from short_drama.schemas.canvas_tool_runs import (
    CanvasToolApprove,
    CanvasToolClaim,
    CanvasToolExecute,
    CanvasToolGuard,
    CanvasToolPrepare,
    CanvasToolRunCreate,
)

from .base import BaseService, utcnow
from .canvas_character_publication_service import CanvasCharacterPublicationService
from .canvas_document import content_hash
from .canvas_editor_tool_service import CanvasEditorToolService
from .canvas_generation_inputs import generation_payload
from .canvas_generation_service import CanvasGenerationService
from .canvas_model_catalog_service import CanvasModelCatalogService
from .canvas_service import iso
from .canvas_storyboard import check_storyboard_context, storyboard_snapshot
from .canvas_text_admission import freeze_text_references

LEASE_TTL = timedelta(seconds=45)


def stage_operation_id(run_id: int | str, stage: str) -> str:
    return f"canvas-tool:{run_id}:{stage}"


class CanvasToolRunService(BaseService):
    model = CanvasToolRun

    def __init__(self, session: Session, settings: Settings) -> None:
        super().__init__(session)
        self.runtime = CanvasToolRunDAO(session)
        self.generation = CanvasGenerationService(session, settings)
        self.canvases = self.generation.canvases
        self.editor_tools = CanvasEditorToolService(session)

    def _owned(
        self, identifier: int, *, include_existing_characters=False
    ) -> tuple[CanvasToolRun, ProjectCanvas]:
        original = self.runtime.run(identifier)
        if original is None:
            raise NotFound("本人分析执行记录不存在")
        canvas = self.canvases.require_canvas(
            original.project_id,
            original.canvas_id,
            lock=True,
            include_existing_characters=include_existing_characters,
        )
        run = self.runtime.run(identifier, lock=True)
        if run is None or run.user_id != self.canvases.actor_id:
            raise NotFound("本人分析执行记录不存在")
        return run, canvas

    @staticmethod
    def _guard(run: CanvasToolRun, guard: CanvasToolGuard) -> None:
        if (
            guard.owner != run.execution_owner
            or guard.execution_epoch != run.execution_epoch
            or run.lease_expires_at is None
            or run.lease_expires_at <= utcnow()
        ):
            raise WorkflowError(
                "canvas_tool_lease_expired", "分析租约已变化，请重新打开后继续", 409
            )

    def _image(self, canvas: ProjectCanvas, state: dict[str, Any]) -> tuple[MediaFile, dict]:
        document = self.canvases._document(canvas, private=True, lock=True)
        node = next((item for item in document["nodes"] if item["id"] == state["nodeId"]), None)
        source = next(
            (item for item in document["nodes"] if item["id"] == state["sourceNodeId"]), None
        )
        inputs = [
            item["fromNodeId"]
            for item in document["connections"]
            if item["toNodeId"] == state["nodeId"]
            and any(
                candidate["id"] == item["fromNodeId"] and candidate["type"] == "image"
                for candidate in document["nodes"]
            )
        ]
        if (
            node is None
            or node["type"] != "ai-art-critique"
            or source is None
            or source["type"] != "image"
            or inputs != [state["sourceNodeId"]]
        ):
            raise WorkflowError("canvas_tool_source_changed", "请保存且仅连接一张已有图片", 409)
        metadata = source.get("metadata") or {}
        key = metadata.get("storageKey") or ""
        if not key.startswith("resource:"):
            raise WorkflowError("canvas_tool_source_changed", "分析图片必须先保存为永久资源", 409)
        media = self.generation.generation._validate_media(key.removeprefix("resource:"), "image")
        if scope_of(self.session, media) != (None, canvas.project_id):
            raise NotFound("分析图片不属于此项目")
        if not media.storage_locator.startswith("minio://"):
            raise WorkflowError("canvas_tool_source_changed", "分析图片必须是永久资源", 409)
        snapshot = {
            "nodeId": source["id"],
            "metadata": {
                field: metadata.get(field)
                for field in ("storageKey", "mimeType", "bytes", "naturalWidth", "naturalHeight")
            },
            "mediaId": str(media.id),
            "checksum": media.checksum_sha256,
            "bytes": media.byte_size,
            "width": media.width,
            "height": media.height,
            "locator": media.storage_locator,
        }
        return media, snapshot

    def _model(self, model_id: int) -> tuple[AIModelConfig, str, str]:
        generation = self.generation.generation
        catalog = CanvasModelCatalogService(self.session, generation.settings)
        catalog.catalog_dao.catalog(catalog.actor_id, lock=True)
        config = generation._config("text", model_id)
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
                    "credential_identity": model_credential_identity(config),
                }
            )
        except GenerationError as error:
            raise GenerationRequestError(error.code) from None
        if adapter not in CANVAS_TOOL_ADAPTERS:
            raise WorkflowError(
                "canvas_generation_protocol_unsupported", "该文字工具协议尚未接通", 422
            )
        secret = catalog.runtime_credentials_locked(config.id, adapter)
        signature = content_hash(
            {
                "id": str(config.id),
                "version": str(config.row_version),
                "provider": config.provider,
                "baseUrl": config.base_url,
                "model": config.model_key,
                "profile": config.runtime_profile,
                "capability": config.capability_cache,
                "authHash": content_hash(secret),
            }
        )
        return config, adapter, signature

    def create(self, payload: CanvasToolRunCreate) -> tuple[dict, bool]:
        payload = CanvasToolRunCreate.model_validate(payload.model_dump(by_alias=True))
        digest = content_hash(payload.model_dump(mode="json", by_alias=True))
        with tool_run_write(self.session), self._transaction(lock_request_project=False):
            canvas = self.generation._canvas(payload.canvas_id)
            existing = self.runtime.by_key(self.canvases.actor_id, payload.client_key)
            if existing is not None:
                if existing.create_hash != digest:
                    raise WorkflowError("canvas_tool_create_conflict", "运行键属于不同的分析", 409)
                return self._detail(existing, canvas), False
            state = payload.state.model_dump(mode="json", by_alias=True)
            if state["kind"] == "storyboard":
                document = self.canvases._document(canvas, private=True, lock=True)
                CanvasCharacterPublicationService(self.canvases).validate(canvas, document)
                snapshot = storyboard_snapshot(document, state["nodeId"])
                media_id = None
            else:
                self.editor_tools.require_enabled_locked("ai-art-critique")
                media, snapshot = self._image(canvas, state)
                media_id = media.id
            _config, _adapter, signature = self._model(payload.state.logical_model_id)
            run = CanvasToolRun(
                **self.canvases.child(canvas),
                user_id=self.canvases.actor_id,
                client_key=payload.client_key,
                create_hash=digest,
                state_json=state,
                source_snapshot=snapshot,
                source_hash=content_hash(snapshot),
                source_media_id=media_id,
                model_config_id=payload.state.logical_model_id,
                config_hash=signature,
                row_version=1,
                execution_epoch=0,
                execution_owner="",
            )
            self.session.add(run)
            self.session.flush()
            return self._detail(run, canvas), True

    def get(self, identifier: int) -> dict:
        with self._transaction():
            return self._detail(*self._owned(identifier))

    def list(self) -> dict:
        with self._transaction(read_only=True):
            return {
                "runs": [
                    self._run(run, self.canvases.require_canvas(run.project_id, run.canvas_id))
                    for run in self.runtime.list(self.canvases.actor_id)
                ]
            }

    def claim(self, identifier: int, command: CanvasToolClaim) -> dict:
        with tool_run_write(self.session), self._transaction():
            run, canvas = self._owned(identifier)
            now = utcnow()
            if run.execution_epoch != command.expected_epoch:
                raise WorkflowError("canvas_tool_epoch_changed", "分析执行版本已变化", 409)
            if (
                run.lease_expires_at is not None
                and run.lease_expires_at > now
                and run.execution_owner != command.owner
            ):
                raise WorkflowError("canvas_tool_lease_held", "分析正在其他窗口执行", 409)
            run.execution_epoch += 1
            run.execution_owner = command.owner
            run.lease_expires_at = now + LEASE_TTL
            run.updated_at, run.updated_by = now, self.canvases.actor_id
            self.session.flush()
            return self._run(run, canvas)

    def heartbeat(self, identifier: int, command: CanvasToolGuard) -> dict:
        with tool_run_write(self.session), self._transaction():
            run, _canvas = self._owned(identifier)
            self._guard(run, command)
            run.lease_expires_at = utcnow() + LEASE_TTL
            run.updated_at, run.updated_by = utcnow(), self.canvases.actor_id
            return {"leaseExpiresAt": iso(run.lease_expires_at)}

    def release(self, identifier: int, command: CanvasToolGuard) -> dict:
        with tool_run_write(self.session), self._transaction():
            run, _canvas = self._owned(identifier)
            self._guard(run, command)
            run.execution_owner, run.lease_expires_at = "", None
            run.updated_at, run.updated_by = utcnow(), self.canvases.actor_id
            return {"released": True}

    def _check_current(
        self, run: CanvasToolRun, canvas: ProjectCanvas
    ) -> tuple[AIModelConfig, str]:
        if run.state_json["kind"] == "storyboard":
            document = self.canvases._document(canvas, private=True, lock=True)
            CanvasCharacterPublicationService(self.canvases).validate(canvas, document)
            snapshot = storyboard_snapshot(document, run.state_json["nodeId"])
        else:
            self.editor_tools.require_enabled_locked("ai-art-critique")
            _media, snapshot = self._image(canvas, run.state_json)
        if content_hash(snapshot) != run.source_hash:
            raise WorkflowError(
                "canvas_tool_source_changed", "原分析图片已变化，请开始新的分析", 409
            )
        config, adapter, signature = self._model(run.model_config_id)
        if signature != run.config_hash:
            raise WorkflowError(
                "canvas_tool_model_changed", "模型执行配置已变化，请开始新的分析", 409
            )
        return config, adapter

    def _check_stage(
        self, run: CanvasToolRun, canvas: ProjectCanvas, command: CanvasToolPrepare
    ) -> CanvasRuntimeTaskCreate:
        payload = command.request
        if run.state_json["kind"] == "storyboard":
            if (
                command.item_key != "storyboard"
                or payload.operation != "storyboard"
                or payload.project_id != canvas.source_key
                or payload.logical_model_id != run.model_config_id
                or payload.input.metadata.node_id != run.state_json["nodeId"]
                or payload.input.metadata.source_node_id != run.state_json["nodeId"]
                or payload.input.metadata.client_operation_id != run.client_key
                or payload.input.metadata.retry_of is not None
            ):
                raise WorkflowError(
                    "canvas_tool_stage_invalid", "分镜来源、模型或稳定身份不同", 422
                )
            check_storyboard_context(
                payload.model_dump(mode="json", by_alias=True, exclude_none=True),
                run.source_snapshot,
            )
            return payload
        canonical = payload.input.agent_requests
        if canonical is None:
            raise WorkflowError("canvas_tool_stage_invalid", "分析阶段需要固定工具请求", 422)
        name = canonical.canonical.tools[0].function.name
        metadata = payload.input.metadata.model_dump(by_alias=True)
        if (
            name == "optimize_prompt"
            or command.item_key != "stage:" + name
            or payload.project_id != canvas.source_key
            or payload.input.metadata.node_id != run.state_json["nodeId"]
            or payload.logical_model_id != run.model_config_id
            or metadata.get("runId") != run.client_key.removeprefix("art-critique:")
            or payload.input.metadata.client_operation_id != stage_operation_id(run.id, name)
            or canonical.canonical.image_keys() != ["resource:" + str(run.source_media_id)]
            or payload.input.metadata.retry_of is not None
        ):
            raise WorkflowError("canvas_tool_stage_invalid", "分析阶段、来源或稳定身份不同", 422)
        builtin_tool(name)
        return payload

    def prepare(self, identifier: int, command: CanvasToolPrepare) -> dict:
        command = CanvasToolPrepare.model_validate(command.model_dump(by_alias=True))
        with tool_run_write(self.session), self._transaction(lock_request_project=False):
            run, canvas = self._owned(identifier, include_existing_characters=True)
            self._guard(run, command)
            payload = self._check_stage(run, canvas, command)
            request = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
            digest = content_hash(request)
            existing = next(
                (
                    item
                    for item in self.runtime.submissions(run.id)
                    if item.item_key == command.item_key
                ),
                None,
            )
            if existing is not None:
                if existing.request_hash != digest:
                    raise WorkflowError("canvas_tool_stage_conflict", "原阶段属于不同输入", 409)
                return self._submission(existing)
            config, adapter = self._check_current(run, canvas)
            prepared = self.generation.generation._prepare(
                "text", generation_payload(payload, canvas.project_id, adapter)
            )
            prepared["canvas_request"] = deepcopy(request)
            freeze_text_references(
                self.generation.generation,
                prepared,
                canvas.project_id,
                adapter,
                config.capability_cache,
            )
            submission = CanvasToolSubmission(
                **self.canvases.child(canvas),
                user_id=run.user_id,
                run_id=run.id,
                item_key=command.item_key,
                request_hash=digest,
                request_json=request,
                execution_json={"model": config.model_key, "configHash": run.config_hash},
            )
            self.session.add(submission)
            self.session.flush()
            return self._submission(submission)

    def approve(self, identifier: int, command: CanvasToolApprove) -> dict:
        with tool_run_write(self.session), self._transaction(lock_request_project=False):
            run, canvas = self._owned(identifier, include_existing_characters=True)
            self._guard(run, command)
            items = {item.id: item for item in self.runtime.submissions(run.id)}
            if any(identifier not in items for identifier in command.submission_ids):
                raise NotFound("本人分析阶段不存在")
            self._check_current(run, canvas)
            selected = [items[identifier] for identifier in command.submission_ids]
            for item in selected:
                if item.approved_at is None:
                    item.approved_at = utcnow()
                    item.updated_at, item.updated_by = utcnow(), self.canvases.actor_id
            return {"submissions": [self._submission(item) for item in selected]}

    def execute(self, identifier: int, command: CanvasToolExecute) -> tuple[dict, bool]:
        with tool_run_write(self.session), self._transaction(lock_request_project=False):
            run, canvas = self._owned(identifier, include_existing_characters=True)
            self._guard(run, command)
            item = next(
                (
                    item
                    for item in self.runtime.submissions(run.id)
                    if item.id == command.submission_id
                ),
                None,
            )
            if item is None:
                raise NotFound("本人分析阶段不存在")
            if item.async_task_id is not None:
                task_id, fresh = item.async_task_id, False
            else:
                if item.approved_at is None:
                    raise WorkflowError("canvas_tool_approval_required", "分析阶段尚未批准", 409)
                self._check_current(run, canvas)
                payload = CanvasRuntimeTaskCreate.model_validate(item.request_json)
                result, fresh = self.generation.create_locked(payload, tool_submission=item)
                task_id = item.async_task_id = int(result["id"])
                item.updated_at, item.updated_by = utcnow(), self.canvases.actor_id
                self.session.flush()
        # 等待项目锁前可能已建立旧快照；提交原子关联后，以新事务读取任务投影。
        return self.generation.detail(task_id), fresh

    def _detail(self, run: CanvasToolRun, canvas: ProjectCanvas) -> dict:
        return {
            "run": self._run(run, canvas),
            "submissions": [self._submission(item) for item in self.runtime.submissions(run.id)],
        }

    @staticmethod
    def _run(run: CanvasToolRun, canvas: ProjectCanvas) -> dict:
        return {
            "id": str(run.id),
            "userId": str(run.user_id),
            "canvasId": canvas.source_key,
            "revision": str(run.row_version),
            "executionEpoch": str(run.execution_epoch),
            "executionOwner": run.execution_owner,
            "leaseExpiresAt": iso(run.lease_expires_at) if run.lease_expires_at else None,
            "status": "running" if run.execution_owner else "idle",
            "state": run.state_json,
            "createdAt": iso(run.created_at),
            "updatedAt": iso(run.updated_at),
        }

    @staticmethod
    def _submission(item: CanvasToolSubmission) -> dict:
        return {
            "id": str(item.id),
            "runId": str(item.run_id),
            "itemKey": item.item_key,
            "requestHash": item.request_hash,
            "execution": item.execution_json,
            "approvedAt": iso(item.approved_at) if item.approved_at else None,
            "taskId": str(item.async_task_id) if item.async_task_id else None,
        }
