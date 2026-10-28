"""固定 BeefTV 声明式目录与内置工作流入口；元数据不等于执行支持。"""

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files

from short_drama.core.exceptions import ConfigurationError, WorkflowError
from short_drama.service.canvas.models.model_catalog import PROTOCOL_ADAPTERS

SOURCE_COMMIT = "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c"
SCOPES = frozenset({"admin.system-channel", "user.custom-channel", "canvas", "creation", "agent"})
CAPABILITIES = frozenset({"text", "image", "video", "audio"})
BUILTIN_RUNNINGHUB_SCOPES = ("user.custom-channel", "canvas")


@lru_cache(maxsize=1)
def bundled_manifest_snapshot() -> dict:
    try:
        snapshot = json.loads(
            files("short_drama.ai")
            .joinpath("data/beeftv_plugin_catalog.json")
            .read_text(encoding="utf-8")
        )
        if snapshot["sourceCommit"] != SOURCE_COMMIT or len(snapshot["packages"]) != 85:
            raise ValueError
        for item in snapshot["packages"]:
            if hashlib.sha256(item["manifestText"].encode()).hexdigest() != item["sourceSha256"]:
                raise ValueError
            json.loads(item["manifestText"])
        return snapshot
    except (OSError, ValueError, KeyError, TypeError):
        raise ConfigurationError("画布官方协议目录缺失或来源校验失败") from None


def _operation(operation: dict | None) -> str | None:
    if operation is None:
        return None
    path = (
        operation.get("path", "").replace("{{model}}", "{model}").replace("{{taskId}}", "{task_id}")
    )
    return operation.get("method", "").upper() + " " + path


def _builtin_runninghub(scope: str, capability: str) -> list[dict]:
    if scope not in BUILTIN_RUNNINGHUB_SCOPES:
        return []
    return [
        {
            "id": f"runninghub-workflow-{kind}",
            "name": f"RunningHub 工作流 / App · {label}",
            "version": "1.0.0",
            "vendor": "内置工作流",
            "categories": [kind],
            "scopes": list(BUILTIN_RUNNINGHUB_SCOPES),
            "enabled": f"runninghub-workflow-{kind}" in PROTOCOL_ADAPTERS,
            "executionSupported": f"runninghub-workflow-{kind}" in PROTOCOL_ADAPTERS,
            "catalogAvailable": True,
            "pluginId": "runninghub-workflow-provider",
            "description": "执行本人已保存的 RunningHub Workflow 或 App，并按字段生成媒体。",
            "parameters": [],
            "sourceKind": "builtin",
            "sourceCommit": SOURCE_COMMIT,
            "sourcePath": "web/src/lib/plugins/builtin/workflows.ts",
            "sourceSha256": "a6ee423edd73a8f134af95d9bf8ee37f376af5a94bb8b633158a055a79758400",
        }
        for kind, label in (("image", "图片"), ("video", "视频"), ("audio", "音频"))
        if not capability or capability == kind
    ]


def provider_catalog(
    scope: str = "user.custom-channel", capability: str | None = None
) -> list[dict]:
    scope, capability = scope.strip(), (capability or "").strip()
    if scope not in SCOPES or capability and capability not in CAPABILITIES:
        raise WorkflowError("canvas_plugin_catalog_filter", "画布协议目录范围或能力无效", 422)
    result = []
    snapshot = bundled_manifest_snapshot()
    for source in snapshot["packages"]:
        manifest = json.loads(source["manifestText"])
        contributions = manifest.get("contributes", {})
        for provider in contributions.get("providers", []):
            if (
                scope not in provider["scopes"]
                or capability
                and capability not in provider["capabilities"]
            ):
                continue
            supported = provider["id"] in PROTOCOL_ADAPTERS
            item = {
                "id": provider["id"],
                "version": manifest["version"],
                "name": provider["label"],
                "vendor": manifest["author"],
                "categories": provider["capabilities"],
                "scopes": provider["scopes"],
                "create": _operation(provider.get("create")),
                "poll": _operation(provider.get("poll")),
                "contentType": provider.get("create", {}).get("contentType"),
                "baseUrl": provider.get("baseUrl"),
                "enabled": supported,
                "catalogAvailable": True,
                "executionSupported": supported,
                "description": manifest.get("description", ""),
                "parameters": deepcopy(provider.get("parameters", [])),
                "workflows": deepcopy(
                    [
                        workflow
                        for workflow in contributions.get("workflows", [])
                        if workflow.get("providerId") == provider["id"]
                    ]
                ),
                "sourceCommit": snapshot["sourceCommit"],
                "sourcePath": source["sourcePath"],
                "sourceSha256": source["sourceSha256"],
            }
            if not supported:
                item["unavailableReason"] = "原版协议元数据已保留，当前 Python 执行协议尚未接通"
            result.append({key: value for key, value in item.items() if value is not None})
    return result + _builtin_runninghub(scope, capability)
