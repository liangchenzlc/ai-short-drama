"""显式私人模型测试使用真实异步执行器，不伪造连接成功。"""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy.orm import Session

from short_drama.ai import GenerationError, select_adapter
from short_drama.ai.adapters import ADAPTER_TYPES
from short_drama.ai.model_identity import model_credential_identity
from short_drama.core.config import Settings
from short_drama.core.exceptions import (
    ConfigurationError,
    GenerationRequestError,
    NotFound,
    WorkflowError,
)
from short_drama.dao.canvas_model_test_dao import CanvasModelTestDAO
from short_drama.domain import AIModelConfig, AsyncTask
from short_drama.schemas.canvas_model_test import CanvasModelTestCreate
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate

from .ai_generation_service import AIGenerationService, resume_action, safe_error
from .base import BaseService
from .canvas_credential_freeze import freeze_canvas_credentials
from .canvas_generation_inputs import generation_payload, normalize_video_request
from .canvas_generation_parameters import frozen_canvas_parameters
from .canvas_model_catalog_service import CanvasModelCatalogService, protocol_adapter
from .canvas_service import iso
from .canvas_task_state import canvas_task_stage
from .canvas_video_admission import VIDEO_ADAPTERS
from .model_discovery_service import normalize_base_url


class CanvasModelTestService(BaseService):
    model = AsyncTask

    def __init__(self, session: Session, settings: Settings) -> None:
        super().__init__(session)
        self.generation = AIGenerationService(session, settings)
        self.catalog = CanvasModelCatalogService(session, settings=settings)
        self.tests = CanvasModelTestDAO(session)

    def _hash(self, payload: CanvasModelTestCreate) -> str:
        body = payload.model_dump(mode="json", by_alias=True)
        channel = body["channel"]
        for name, alias in (("api_key", "apiKey"), ("secret_key", "secretKey")):
            value = getattr(payload.channel, name)
            channel[alias] = value.get_secret_value() if value else None
        channel["headers"] = [
            {"name": item.name, "value": item.value.get_secret_value()}
            for item in payload.channel.headers
        ]
        return self.generation._hash(payload.mode, body, "canvas.model-test")

    def _draft_locked(self, payload: CanvasModelTestCreate) -> tuple[AIModelConfig, str, dict]:
        channel = payload.channel
        if channel.source_key.startswith("host-"):
            identifier = channel.source_key.removeprefix("host-")
            config = self.generation._config(payload.mode, identifier)
            if config.model_key != payload.model:
                raise NotFound("本人模型配置不存在")
            source_url = f"/api/v1/canvas-runtime/ai/models/{identifier}"
            if channel.base_url != source_url and normalize_base_url(
                channel.base_url
            ) != normalize_base_url(config.base_url):
                raise WorkflowError(
                    "canvas_model_test_address_changed", "宿主模型地址已改变，请重新读取配置", 409
                )
            return self._saved_host_test_locked(config)
        elif channel.source_key == "beefapi":
            catalog = self.catalog.catalog_dao.catalog(self.catalog.actor_id)
            stored = next(
                (
                    item
                    for item in (catalog.channels_json if catalog else [])
                    if item["id"] == "beefapi"
                ),
                None,
            )
            if stored is None or payload.model not in stored["models"]:
                raise NotFound("本人 BeefAPI 模型不存在")
            selected = next(
                (item for item in stored["modelProfiles"] if item["model"] == payload.model), None
            )
            if selected is None or selected.get("capability") != payload.mode:
                raise NotFound("本人 BeefAPI 模型能力不存在")
            previous = self.catalog._beefapi_credentials_locked(channel.base_url)
            public = stored
        else:
            if channel.credential_ref:
                raise WorkflowError(
                    "canvas_model_legacy_credential_reference",
                    "旧渠道凭据引用已停用，请使用宿主 AI 配置中的模型身份",
                    409,
                )
            previous = {}
            public = None
        sanitized, secret = self.catalog._channel(channel, previous)
        if public is not None:
            sanitized["baseUrl"] = public["baseUrl"]
            if channel.source_key == "beefapi":
                sanitized["modelProfiles"] = public["modelProfiles"]
        if not secret["apiKey"]:
            raise WorkflowError("canvas_model_test_credential_required", "请先填写 API Key", 422)
        profile = next(
            item for item in sanitized["modelProfiles"] if item["model"] == payload.model
        )
        expected = protocol_adapter(sanitized, profile)
        if ADAPTER_TYPES.get(expected) != payload.mode:
            raise WorkflowError(
                "canvas_generation_protocol_unsupported",
                "该渠道协议尚未接通，未发送供应商请求",
                422,
            )
        config = self.catalog._update_model(sanitized, profile, secret, None)
        snapshot = {
            key: getattr(config, key)
            for key in ("base_url", "model_key", "service_type", "capability_cache")
        }
        snapshot["credential_identity"] = model_credential_identity(config)
        try:
            adapter = select_adapter(snapshot)
        except GenerationError as error:
            raise GenerationRequestError(error.code) from None
        if adapter != expected:
            raise WorkflowError(
                "canvas_generation_protocol_unsupported", "渠道协议与模型能力不一致", 422
            )
        return config, adapter, secret

    def _saved_host_test_locked(self, saved):
        """宿主测试冻结已保存配置，不采用浏览器声明的协议、能力或密钥。"""
        from .model_runtime_config import (
            PROTOCOL_ADAPTERS,
            decrypt_runtime_credentials,
            refresh_runtime_model,
        )

        binding = self.catalog.catalog_dao.binding(saved.id)
        channel_key = binding.channel_key if binding else f"host-{saved.id}"
        derived = SimpleNamespace(
            **{
                key: deepcopy(getattr(saved, key))
                for key in (
                    "base_url",
                    "model_key",
                    "service_type",
                    "capability_cache",
                    "runtime_profile",
                    "apikey",
                    "runtime_credentials_cipher",
                )
            }
        )
        try:
            refresh_runtime_model(derived, channel_key)
        except GenerationError as error:
            raise GenerationRequestError(error.code) from None
        snapshot = {
            key: getattr(derived, key)
            for key in ("base_url", "model_key", "service_type", "capability_cache")
        }
        snapshot["credential_identity"] = model_credential_identity(saved)
        try:
            adapter = select_adapter(snapshot)
        except GenerationError as error:
            raise GenerationRequestError(error.code) from None
        runtime = saved.runtime_profile or {}
        protocol = runtime.get("protocol") or next(
            (key for key, value in PROTOCOL_ADAPTERS.items() if value == adapter), None
        )
        if protocol is None:
            raise WorkflowError(
                "canvas_generation_protocol_unsupported", "该模型测试协议尚未接通", 422
            )
        secret = self.catalog.runtime_credentials_locked(saved.id, adapter)
        if secret is None:
            try:
                cipher = self.catalog.configs._key_cipher()
                secret = {
                    "apiKey": cipher.decrypt(saved.apikey) if saved.apikey else "",
                    **decrypt_runtime_credentials(saved, cipher),
                }
            except ValueError:
                raise ConfigurationError("本人模型凭据无法解密") from None
        profile = {
            "model": saved.model_key,
            "displayName": saved.name,
            "capability": saved.service_type,
            "protocol": protocol,
        }
        for public, key in (
            ("capabilityConfig", "capability_config"),
            ("defaultOptions", "default_options"),
            ("logicalCapabilitySpec", "logical_capability_spec"),
            ("logicalCapabilityProfiles", "logical_capability_profiles"),
            ("videoCapabilitiesVersion", "video_capabilities_version"),
        ):
            if runtime.get(key) is not None:
                profile[public] = deepcopy(runtime[key])
        channel = {
            "id": channel_key,
            "baseUrl": saved.base_url,
            "enabled": True,
            "apiFormat": runtime.get("api_format", "openai"),
        }
        test = self.catalog._update_model(channel, profile, secret, None)
        self.session.flush()
        return test, adapter, secret

    def create(self, payload: CanvasModelTestCreate, key: str) -> tuple[dict, bool]:
        payload = CanvasModelTestCreate.model_validate(payload.model_dump())
        if key != payload.client_operation_id:
            raise WorkflowError("canvas_operation_key_mismatch", "测试操作身份与请求键不一致", 422)
        key = "canvas-model-test:" + hashlib.sha256(key.encode()).hexdigest()
        digest = self._hash(payload)
        with self._transaction():
            existing = self.generation._existing(key, digest)
            if existing:
                return self._project_locked(int(existing[0]["generation_id"])), False
            config, adapter, secret = self._draft_locked(payload)
            self.session.flush()
            canvas_request = CanvasRuntimeTaskCreate.model_validate(
                {
                    "projectId": "model-connection-test",
                    "type": f"canvas_{payload.mode}",
                    "operation": "text_to_video" if payload.mode == "video" else payload.mode,
                    "prompt": payload.prompt,
                    "logicalModelId": str(config.id),
                    "input": {
                        "mode": payload.mode,
                        "prompt": payload.prompt,
                        "config": payload.config,
                        "textOptions": payload.text_options.model_dump(by_alias=True),
                        "metadata": {
                            "nodeId": "model-connection-test",
                            "clientOperationId": payload.client_operation_id,
                        },
                    },
                }
            )
            if adapter in VIDEO_ADAPTERS:
                canvas_request = normalize_video_request(canvas_request, config.capability_cache)
            request = generation_payload(canvas_request, self.catalog.actor_id, adapter)
            request.pop("project_id", None)

            def freeze(prepared):
                prepared["source"] = {
                    "scene": "canvas_model_test",
                    "channel_key": payload.channel.source_key,
                }
                prepared["canvas_request"] = canvas_request.model_dump(mode="json", by_alias=True)
                parameters = frozen_canvas_parameters(canvas_request, adapter)
                if parameters is not None:
                    prepared["canvas_parameters"] = parameters
                return prepared

            summary, fresh = self.generation.create_locked(
                payload.mode, request, key, request_hash=digest, prepare_transform=freeze
            )
            record = self.generation.record_dao.for_task(int(summary["generation_id"]))[0]
            record.config_snapshot = {**record.config_snapshot, "canvas_model_test": True}
            freeze_canvas_credentials(record, secret, self.catalog.configs._key_cipher())
            # The frozen call owns its encrypted credential; test-only configs do not
            # become selectable defaults or appear in the normal model library.
            config.is_deleted, config.enabled = 1, 0
            self.session.flush()
            return self._project_locked(int(summary["generation_id"])), fresh

    def _project_locked(self, identifier: int) -> dict:
        task = self._require(AsyncTask, identifier, for_update=False)
        records = self.generation.record_dao.for_task(task.id)
        if (
            task.initiated_by != self.catalog.actor_id
            or task.scope_user_id != self.catalog.actor_id
            or task.project_id is not None
            or not records
            or (records[0].request_data.get("source") or {}).get("scene") != "canvas_model_test"
            or records[0].config_snapshot.get("canvas_model_test") is not True
        ):
            raise NotFound("本人模型测试不存在")
        error = safe_error(task.error)
        result = None
        if task.status == "succeeded":
            result = {"mode": task.service_type}
            if task.service_type == "text":
                result["text"] = records[-1].text_content or ""
            else:
                rows = self.tests.media([record.id for record in records])
                media = []
                for asset, file in rows:
                    item = {
                        "outputIndex": asset.output_index,
                        "dataUrl": f"/api/v1/canvas-runtime/resources/{file.id}/file",
                        "url": f"/api/v1/canvas-runtime/resources/{file.id}/file",
                        "storageKey": f"resource:{file.id}",
                        "bytes": file.byte_size,
                        "mimeType": file.format_code,
                    }
                    for name, value in (
                        ("width", file.width),
                        ("height", file.height),
                        ("durationMs", file.duration_ms),
                    ):
                        if value is not None:
                            item[name] = value
                    media.append(item)
                if task.service_type == "image":
                    result["images"] = media
                elif media:
                    result[task.service_type] = media[0]
        return {
            "id": str(task.id),
            "status": task.status,
            "result": deepcopy(result),
            "error": error["message"] if error else None,
            "errorCode": error["code"] if error else None,
            "canCancel": task.status in {"queued", "running"} and not task.cancel_requested,
        }

    def runtime_locked(self, identifier: int) -> dict:
        """原任务中心投影；不创建画布或把测试媒体发布给项目成员。"""
        projected = self._project_locked(identifier)
        task = self._require(AsyncTask, identifier, for_update=False)
        records = self.generation.record_dao.for_task(task.id)
        first, latest = records[0], records[-1]
        request = first.request_data["canvas_request"]
        input_value = deepcopy(request["input"])
        input_value["metadata"] = {
            "source": "model-connection-test",
            "clientOperationId": input_value["metadata"]["clientOperationId"],
        }
        stage = canvas_task_stage(task, latest)
        result = projected["result"]
        value = {
            "id": str(task.id),
            "type": request["type"],
            "operation": request["operation"],
            "status": task.status,
            "progress": 100 if task.status == "succeeded" else 0,
            "stage": stage,
            "prompt": request["prompt"],
            "provider": first.config_snapshot.get("provider"),
            "model": first.config_snapshot.get("model_key"),
            "clientOperationId": input_value["metadata"]["clientOperationId"],
            "clientContext": {"source": "model-connection-test"},
            "inputJson": json.dumps(input_value, ensure_ascii=False, separators=(",", ":")),
            "resultState": "READY" if result is not None else "NOT_AVAILABLE",
            "outputs": [],
            "error": projected["error"],
            "errorCode": projected["errorCode"],
            "providerRequestId": latest.provider_task_id,
            "canRetry": False,
            "canResume": bool(resume_action(task, latest)),
            "canCancel": projected["canCancel"],
            "attempts": len(records),
            "createdAt": iso(task.created_at),
            "updatedAt": iso(task.updated_at),
            "startedAt": iso(task.started_at) if task.started_at else None,
            "completedAt": iso(task.finished_at) if task.finished_at else None,
        }
        if result is not None:
            value["resultJson"] = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            if task.service_type == "text":
                value["textDraft"] = result["text"]
            else:
                media = (
                    result.get("images", [])
                    if task.service_type == "image"
                    else [result[task.service_type]]
                )
                value["outputs"] = [
                    {"outputIndex": item["outputIndex"], "mediaType": task.service_type}
                    for item in media
                ]
                if media and task.service_type in {"image", "video"}:
                    value.update(previewUrl=media[0]["url"], previewKind=task.service_type)
        return value

    def detail(self, identifier: int) -> dict:
        with self._transaction(read_only=True):
            return self._project_locked(identifier)

    def cancel(self, identifier: int) -> dict:
        self.detail(identifier)
        self.generation.cancel(identifier)
        return self.detail(identifier)
