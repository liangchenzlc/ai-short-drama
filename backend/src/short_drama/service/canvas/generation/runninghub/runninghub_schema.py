"""从本人保存的配置读取 RunningHub 公开字段，不调用生成接口。"""

import json
import time
from types import SimpleNamespace

from pydantic import ValidationError

from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
    payload_code,
    runninghub_root,
    workflow_prompt,
)
from short_drama.ai.adapters.runninghub.fields import collect_app_fields, collect_fields
from short_drama.ai.gateway.transport import SafeTransport
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import AIModelConfig
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.canvas.generation.runninghub.runninghub import (
    RunningHubSchemaRequest,
    RunningHubWorkflow,
)
from short_drama.service.ai_config.management.runtime import runtime_protocol_adapter
from short_drama.service.canvas.models.model_catalog import CanvasModelCatalogService


class CanvasRunningHubSchemaService:
    def __init__(self, session, settings):
        self.catalog = CanvasModelCatalogService(session, settings=settings)
        self.transport = SafeTransport(
            SimpleNamespace(generation_allowed_hosts=settings.model_discovery_allowed_hosts)
        )

    def read(self, identifier: str, payload: RunningHubSchemaRequest) -> RunningHubWorkflow:
        catalog = self.catalog
        with catalog._transaction(read_only=True):
            model = catalog._require(AIModelConfig, parse_identifier(identifier), for_update=False)
            if model.owner_user_id != catalog.actor_id or model.is_deleted:
                raise NotFound("本人模型配置不存在")
            if model.row_version != payload.row_version:
                raise Conflict("模型配置已更新，请重新读取后获取工作流字段")
            profile = model.runtime_profile or {}
            adapter = runtime_protocol_adapter(profile, "", model.model_key, model.service_type)
            if adapter not in RUNNINGHUB_ADAPTERS:
                raise WorkflowError(
                    "canvas_workflow_protocol_invalid", "请先保存内置 RunningHub 协议", 422
                )
            descriptor = RunningHubWorkflow.model_validate(profile.get("runninghub_workflow") or {})
            descriptor.workflow_id = descriptor.workflow_id.strip() or model.model_key.strip()
            if descriptor.kind == "app":
                descriptor.webapp_id = descriptor.webapp_id.strip() or descriptor.workflow_id
            secret = catalog.runtime_credentials_locked(model.id, adapter) or {}
            base, mode = model.base_url, model.service_type
        key = secret.get("apiKey", "").strip()
        if not key:
            raise WorkflowError(
                "canvas_workflow_key_missing", "请先保存 RunningHub 积分 API Key", 422
            )
        headers = {"Accept": "application/json", **secret.get("headers", {})}
        identity = descriptor.webapp_id if descriptor.kind == "app" else descriptor.workflow_id
        suffix = (
            "/api/webapp/apiCallDemo"
            if descriptor.kind == "app"
            else "/api/openapi/getJsonApiFormat"
        )
        try:
            status, _, data = self.transport.request(
                "POST",
                runninghub_root(base) + suffix,
                headers=headers,
                body={
                    "apiKey": key,
                    "webappId" if descriptor.kind == "app" else "workflowId": identity,
                },
                max_bytes=2 * 1024**2,
                deadline=time.monotonic() + 30,
            )
            if not 200 <= status < 300:
                raise GenerationError(
                    "provider_auth" if status in {401, 403} else "provider_rejected"
                )
            raw = json.loads(data)
            if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
                raise GenerationError("invalid_workflow_schema")
            if descriptor.kind == "app":
                if payload_code(raw) not in {None, 0}:
                    raise GenerationError("invalid_workflow_schema")
                descriptor.workflow_json = {}
                descriptor.fields = collect_app_fields(raw["data"].get("nodeInfoList"), mode)
            else:
                # 固定源设置页允许空 prompt，并不将 payload code 当失败。
                descriptor.workflow_json = workflow_prompt(raw, required=False)
                descriptor.fields = collect_fields(descriptor.workflow_json, mode)
            descriptor.title = descriptor.title.strip() or identity
            public = descriptor.model_dump(mode="json", by_alias=True, exclude_none=True)
            serialized = json.dumps(public, ensure_ascii=False)
            secrets = [key, secret.get("secretKey", ""), *secret.get("headers", {}).values()]
            if any(value and value in serialized for value in secrets):
                raise GenerationError("invalid_workflow_schema")
            return RunningHubWorkflow.model_validate(public)
        except (GenerationError, ValueError, TypeError, ValidationError) as error:
            reason = error.code if isinstance(error, GenerationError) else "invalid_workflow_schema"
            messages = {
                "provider_auth": "读取 RunningHub 字段鉴权失败，请检查已保存的积分 API Key",
                "unsafe_address": "RunningHub 服务地址无效或不允许访问",
                "timeout": "读取 RunningHub 字段超时，请重试",
            }
            raise WorkflowError(
                "canvas_workflow_schema_unavailable",
                messages.get(reason, "读取 RunningHub 字段失败，请检查服务地址与工作流身份"),
                504 if reason == "timeout" else 502,
            ) from None
