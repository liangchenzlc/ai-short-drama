"""Opt-in live fixtures: real text, graph, MySQL/MinIO and restricted SDK sandboxes."""

import base64
import io
import json
import os
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai._streaming import SSEDecoder
from opensandbox import SandboxSync
from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule
from pydantic import SecretStr
from sqlalchemy import select

from short_drama.agent.runtime import executor
from short_drama.agent.runtime.trace_content import display_safe, utf8_preview
from short_drama.agent.sandbox.workspace import cleanup_run
from short_drama.agent.tools.registry import get_spec
from short_drama.api.dependencies.session import get_session
from short_drama.api.v1.assistant.conversations.trace import router as trace_router
from short_drama.api.v1.assistant.operations.questions import router as question_router
from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError
from short_drama.core.time import utcnow
from short_drama.domain import AIModelConfig
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.service.ai_config.management.assistant_models import AgentModelService
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.assistant.settings.preferences import AssistantSettingsService
from short_drama.service.projects.management.project import ProjectService
from short_drama.service.skills.catalog.skill import AgentSkillService
from short_drama.storage.minio import MinioStorage
from tests.integration.standard.assets.test_asset_image_generation import MemoryStorage
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    task_actor,
    task_service,
)
from tests.support.assistant_live import RunLedgerObservation


def validate_live_target(root: str, model: str) -> None:
    authorized_model = {
        "https://t8api.com": "gpt-6.1-terra",
        "https://sub2.de5.net": "gpt-6.1-terra",
        "https://api.deepseek.com": "deepseek-flash",
    }.get(root.rstrip("/").lower())
    if authorized_model is None:
        raise RuntimeError("不在本次授权的站点范围")
    if model != authorized_model:
        raise RuntimeError("不在本次授权的模型范围")


def live_text_config():
    if os.environ.get("RUN_AGENT_RUNTIME_LIVE") != "1":
        raise RuntimeError("真实文本验收尚未启用")
    root, model = os.environ["AGENT_LIVE_URL"].rstrip("/"), os.environ["AGENT_LIVE_MODEL"]
    validate_live_target(root, model)
    return root, model, SecretStr(os.environ["AGENT_LIVE_API_KEY"])


def diagnostic_preview(value, secret, maximum=12000):
    safe = display_safe(value).replace(secret, "[已隐藏]")
    preview = utf8_preview(safe, maximum)
    return {"preview": preview, "truncated": preview != safe}


def model_diagnostic(turn, secret):
    source = turn.usage.get("source")
    body = turn.request_messages[0]["body"] if turn.request_messages else {}
    result = {
        "id": str(turn.id),
        "run_id": str(turn.run_id),
        "scope_id": str(turn.scope_id),
        "source": source,
    }
    if source == "tool_selection":
        # 只保存目录身份，便于区分未授权、未提供和模型未选中。
        candidates = []
        for tool in body.get("tools", []):
            schema = tool.get("function", {}).get("parameters", {})
            choices = schema.get("properties", {}).get("tools", {}).get("items", {})
            for choice in [choices, *choices.get("anyOf", [])]:
                candidates.extend(choice.get("enum", []))
                candidates.extend([choice["const"]] if "const" in choice else [])
        result["candidate_tool_names"] = [
            diagnostic_preview(name, secret, 160)["preview"]
            for name in candidates[:64]
            if isinstance(name, str)
        ]
        result["omitted_candidate_tools"] = max(0, len(candidates) - 64)
        query = "\n".join(
            item["content"]
            for item in body.get("messages", [])
            if item.get("role") == "user" and isinstance(item.get("content"), str)
        )
        result["query"] = diagnostic_preview(query, secret)
        arguments, selected = {}, []
        raw = (turn.response or {}).get("raw", {})
        try:
            data = base64.b64decode(raw["body_b64"], validate=True)
            payloads = (
                [
                    event.json()
                    for event in SSEDecoder().iter_bytes(iter([data]))
                    if event.data and event.data != "[DONE]"
                ]
                if raw["content_type"].startswith("text/event-stream")
                else [json.loads(data)]
            )
            for payload in payloads:
                for choice in payload.get("choices", []):
                    message = choice.get("message", choice.get("delta", {}))
                    for index, call in enumerate(message.get("tool_calls", [])):
                        index = call.get("index", index)
                        arguments[index] = arguments.get(index, "") + call.get("function", {}).get(
                            "arguments", ""
                        )
            for value in arguments.values():
                selected.extend(json.loads(value).get("tools", []))
            result["selection_parsed"] = bool(arguments)
        except (KeyError, TypeError, ValueError, UnicodeError):
            result["selection_parsed"] = False
        result["selected_tool_names"] = [
            diagnostic_preview(name, secret, 160)["preview"]
            for name in selected[:32]
            if isinstance(name, str)
        ]
    elif source == "assistant":
        names = [item.get("function", {}).get("name", "") for item in body.get("tools", [])]
        result["bound_tool_names"] = [
            diagnostic_preview(name, secret, 160)["preview"] for name in names[:64]
        ]
        result["omitted_bound_tools"] = max(0, len(names) - 64)
        message = (turn.response or {}).get("graph_message", {}).get("data", {})
        if not message.get("tool_calls") and isinstance(message.get("content"), str):
            result["final_reply"] = diagnostic_preview(message["content"], secret, 2000)
    return result


class NamespaceStorage:
    def __init__(self, storage, prefix):
        self.storage, self.prefix = storage, prefix

    def key(self, key):
        return key if key.startswith(self.prefix) else self.prefix + key

    def put(self, bucket, key, *args):
        return self.storage.put(bucket, self.key(key), *args)

    def stat(self, bucket, key):
        return self.storage.stat(bucket, self.key(key))

    def open(self, bucket, key):
        return self.storage.open(bucket, self.key(key))

    def presigned_get(self, bucket, key, expiry):
        return self.storage.presigned_get(bucket, self.key(key), expiry)


class ControlledStorage(MemoryStorage):
    @contextmanager
    def open(self, bucket, key):
        data, _mime = self.objects[bucket, key]
        with io.BytesIO(data) as source:
            yield SimpleNamespace(
                read=source.read,
                stream=lambda size: iter(lambda: source.read(size), b""),
            )


@dataclass
class LiveCase:
    factory: object
    settings: Settings
    model_id: int
    root: str
    model: str
    storage: object
    objects: object
    directory: Path
    scene_id: str
    observer: object
    text_secret: SecretStr = field(repr=False)
    network_policy: object = None
    started: float = field(default_factory=time.monotonic)
    runs: list[int] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    passed: bool = False

    def create(self, goal, *, references=(), skills=(), project_id=None, media=False, **extra):
        with self.factory() as session:
            service = task_service(session, self.settings)
            if project_id is None:
                project_id = (
                    ProjectService(session)
                    .create_project(
                        {"name": self.scene_id, "workspace_mode": "standard"},
                        idempotency_key=uuid4().hex,
                    )
                    .id
                )
            preferences = AssistantSettingsService(session, self.settings)
            preferences.update_settings(
                {
                    "settings_version": str(preferences.get_settings().settings_version),
                    "media_generation_enabled": media,
                }
            )
            conversation = service.create_global_conversation({}, uuid4().hex)
            accepted = service.send_task_message(
                conversation.id,
                {
                    "content": goal,
                    "model_config_id": str(self.model_id),
                    "execution_target": {"mode": "standard", "project_id": str(project_id)},
                    "subject_selection": {
                        "expected_input_revision": "0",
                        "subjects": list(references),
                    },
                    "skills": [
                        {"id": str(item.id), "content_version": str(item.content_version)}
                        for item in skills
                    ],
                    **extra,
                },
                uuid4().hex,
            )
        self.runs.append(accepted.run.id)
        return conversation, accepted, int(project_id)

    def execute(self, accepted, *, fault=None, controlled_replies=None, answer=None):
        with self.factory() as session:
            version = session.get(AgentRun, accepted.run.id).message_version
        payload = {
            "database_url": self.factory.kw["bind"].url.render_as_string(hide_password=False),
            "settings": self.settings.model_dump(mode="python"),
            "root": self.root,
            "model": self.model,
            "api_key": self.text_secret.get_secret_value(),
            "prefix": self.objects.prefix,
            "network_policy": self.network_policy.model_dump(mode="json"),
            "run_id": accepted.run.id,
            "message_version": version,
            "fault": fault,
            "controlled_replies": controlled_replies,
            "answer": answer,
        }
        if controlled_replies is not None and hasattr(self.storage, "objects"):
            payload["controlled_objects"] = [
                [bucket, key, base64.b64encode(data).decode(), mime]
                for (bucket, key), (data, mime) in self.storage.objects.items()
            ]
        process_env = {
            key: value for key, value in os.environ.items() if key != "AGENT_LIVE_API_KEY"
        }
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "tests.support.agent_runtime_live_worker"],
            input=json.dumps(
                payload,
                ensure_ascii=False,
                default=lambda value: value.get_secret_value(),
            ),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=process_env,
            timeout=180 if controlled_replies is not None else 4 * 60 * 60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        secrets = [self.text_secret.get_secret_value(), self.factory.kw["bind"].url.password]
        secrets.extend(
            value.get_secret_value()
            for value in self.settings.model_dump(mode="python").values()
            if isinstance(value, SecretStr)
        )
        error = completed.stderr
        for secret in filter(None, secrets):
            error = error.replace(secret, "[redacted]")
        assert completed.returncode == 0, error[-6000:]
        delivery = json.loads(completed.stdout)
        assert delivery["pid"] != os.getpid()
        self.facts.setdefault("worker_deliveries", []).append(delivery)
        with self.factory() as session:
            return session.get(AgentRun, accepted.run.id)

    def skill(self, name, instructions, resources=(), **metadata):
        with self.factory() as session:
            session.info["actor"] = task_actor()
            return AgentSkillService(
                session, self.settings, self.objects, tool_lookup=get_spec
            ).create(
                {
                    "name": name,
                    "slug": "live-" + uuid4().hex,
                    "description": f"本次创作可按需使用的{name}。",
                    "instructions": instructions,
                    "resources": list(resources),
                    **metadata,
                },
                uuid4().hex,
            )

    def operations(self, accepted):
        with self.factory() as session:
            return session.scalars(
                select(AgentOperation)
                .where(
                    AgentOperation.run_id == accepted.run.id,
                )
                .order_by(AgentOperation.id)
            ).all()

    def stop(self, accepted, key=None):
        with self.factory() as session:
            return task_service(session, self.settings).control_task(
                accepted.task.id, {"kind": "stop"}, key or uuid4().hex
            )

    def question_api(self):
        return acceptance_api(self.factory, self.settings, self.objects)

    def followup(self, accepted, content, **extra):
        with self.factory() as session:
            result = task_service(session, self.settings).send_task_message(
                accepted.task.conversation_id,
                {"task_id": str(accepted.task.id), "content": content, **extra},
                uuid4().hex,
            )
        if result.run.id not in self.runs:
            self.runs.append(result.run.id)
        return result

    def report(self):
        report = {
            "scenario_id": self.scene_id,
            "site": self.root,
            "model": self.model,
            "passed": False,
            "test_body_passed": self.passed,
            "duration_seconds": round(time.monotonic() - self.started, 2),
            "calls_by_source": None,
            "tool_counts": None,
            "runs": None,
            "scopes": None,
            "model_receipts": None,
            "model_diagnostics": [],
            "omitted_model_diagnostics": None,
            "actual_provider_price": "unknown",
            "facts": self.facts,
            "report_collection": {"status": "collecting"},
        }
        self._write_report(report)
        stage = "runs"
        try:
            with self.factory() as session:
                runs = session.execute(
                    select(
                        AgentRun.id,
                        AgentRun.status,
                        AgentRun.error,
                        AgentRun.usage,
                        AgentRun.model_config_id,
                    ).where(AgentRun.id.in_(self.runs))
                ).all()
                report["runs"] = [
                    {
                        "id": str(run.id),
                        "status": run.status,
                        "error": run.error,
                        "usage": run.usage,
                    }
                    for run in runs
                ]
                self._write_report(report)
                assert (
                    session.scalar(
                        select(AIModelConfig.id)
                        .where(
                            AIModelConfig.id.in_(run.model_config_id for run in runs),
                            AIModelConfig.apikey.is_not(None),
                        )
                        .limit(1)
                    )
                    is None
                )
                stage = "model_receipts"
                turn_ids = self._report_receipts(session, report)
                self._write_report(report)
                stage = "operations_and_scopes"
                report["tool_counts"] = dict(
                    Counter(
                        session.scalars(
                            select(AgentOperation.tool_name).where(
                                AgentOperation.run_id.in_(self.runs)
                            )
                        )
                    )
                )
                scopes = session.execute(
                    select(
                        AgentExecutionScope.id,
                        AgentExecutionScope.parent_scope_id,
                        AgentExecutionScope.sandbox_status,
                    ).where(AgentExecutionScope.run_id.in_(self.runs))
                ).all()
                report["scopes"] = [
                    {
                        "id": str(scope.id),
                        "parent": str(scope.parent_scope_id) if scope.parent_scope_id else None,
                        "sandbox_status": scope.sandbox_status,
                    }
                    for scope in scopes
                ]
                self._write_report(report)
                stage = "model_diagnostics"
                diagnostic_ids = (
                    turn_ids if len(turn_ids) <= 128 else [*turn_ids[:16], *turn_ids[-112:]]
                )
                report["omitted_model_diagnostics"] = max(0, len(turn_ids) - 128)
                for turn_id in diagnostic_ids:
                    turn = session.get(AgentTurn, turn_id)
                    report["model_diagnostics"].append(
                        model_diagnostic(turn, self.text_secret.get_secret_value())
                    )
            report["passed"] = self.passed
            report["report_collection"] = {"status": "complete"}
        except Exception as error:
            details = {"type": type(error).__name__}
            arguments = getattr(getattr(error, "orig", None), "args", ())
            if arguments and type(arguments[0]) is int:
                details["driver_code"] = arguments[0]
            report["report_collection"] = {"status": "failed", "stage": stage, "error": details}
            self._write_report(report)
            raise
        self._write_report(report)

    def _report_receipts(self, session, report):
        # MySQL filesort不能携带大JSON；只排序ID，后续按ID逐条读回。
        turn_ids = session.scalars(
            select(AgentTurn.id)
            .where(AgentTurn.run_id.in_(self.runs))
            .order_by(AgentTurn.started_at, AgentTurn.id)
        ).all()
        receipts = []
        for turn_id in turn_ids:
            turn = session.execute(
                select(AgentTurn.id, AgentTurn.status, AgentTurn.usage).where(
                    AgentTurn.id == turn_id
                )
            ).one()
            receipts.append(
                {
                    "id": str(turn.id),
                    "source": turn.usage.get("source"),
                    "status": turn.status,
                    "usage": turn.usage,
                }
            )
        report["model_receipts"] = receipts
        report["calls_by_source"] = dict(Counter(item["source"] or "unknown" for item in receipts))
        return turn_ids

    def _write_report(self, report):
        self.directory.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        assert self.text_secret.get_secret_value() not in encoded
        path = self.directory / f"{self.scene_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(path)
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) != 5 or attempt == 4:
                    raise
                time.sleep(0.05)


def acceptance_api(factory, settings, objects=None, *, raise_server_exceptions=True):
    app = FastAPI()
    app.state.settings, app.state.storage = settings, objects
    app.include_router(question_router, prefix="/assistant")
    app.include_router(trace_router, prefix="/assistant")

    def session_dependency():
        with factory() as session:
            session.info["actor"] = task_actor()
            yield session

    @app.exception_handler(BusinessError)
    async def error_handler(request, error):
        from fastapi.responses import JSONResponse

        return JSONResponse({"code": error.code}, status_code=error.status_code)

    app.dependency_overrides[get_session] = session_dependency
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def controlled_case(task_workspace, tmp_path):
    from opensandbox.models.sandboxes import NetworkPolicy

    factory, _settings, model_id = task_workspace
    settings = Settings(_env_file=None, auth_enabled=True, agent_enabled=True, minio_endpoint=None)
    with factory() as session:
        task_service(session, settings)
        AIModelConfigService(session).update(
            model_id,
            {
                "row_version": "1",
                "model_key": "gpt-6.1-terra",
                "base_url": "https://t8api.com/v1",
                "runtime_profile": {
                    "version": 1,
                    "api_format": "openai",
                    "protocol": "chat-completion",
                },
            },
        )
    storage = ControlledStorage()
    return LiveCase(
        factory,
        settings,
        model_id,
        "https://t8api.com",
        "gpt-6.1-terra",
        storage,
        NamespaceStorage(storage, "controlled-runner/"),
        tmp_path,
        "controlled-worker",
        None,
        SecretStr("controlled-text-key-not-persisted"),
        NetworkPolicy(default_action="deny", egress=[]),
    )


@pytest.fixture
def live_case(task_workspace, monkeypatch, request):
    factory, _fixture_settings, model_id = task_workspace
    root, model, secret = live_text_config()
    settings = Settings(_env_file=None, auth_enabled=True, agent_enabled=True)
    assert settings.minio_endpoint and settings.agent_sandbox_domain
    assert settings.agent_sandbox_api_key, "live需要已验证的专用OpenSandbox服务"
    settings.model_discovery_allowed_hosts = [urlsplit(root).hostname]
    policy = NetworkPolicy(
        default_action="deny",
        egress=[
            NetworkRule(action="allow", target=host)
            for host in (
                "pypi.org",
                "files.pythonhosted.org",
                "registry.npmjs.org",
                *filter(None, os.environ.get("AGENT_LIVE_FIXTURE_HOST", "").split(",")),
            )
        ],
    )
    create = SandboxSync.create

    def restricted_create(*args, **kwargs):
        kwargs["network_policy"] = policy
        return create(*args, **kwargs)

    monkeypatch.setattr(SandboxSync, "create", restricted_create)

    def credential(snapshot, key):
        if snapshot["model_key"] != model or snapshot["base_url"] != root + "/v1":
            raise RuntimeError("live凭据仅用于本次冻结的文本模型")
        return secret.get_secret_value()

    monkeypatch.setattr(executor, "model_credentials", credential)
    with factory() as session:
        session.info["actor"] = task_actor()
        updated = AIModelConfigService(session).update(
            model_id,
            {
                "row_version": "1",
                "model_key": model,
                "provider": "deepseek" if model == "deepseek-flash" else "compatible",
                "base_url": root + "/v1",
                "runtime_profile": {
                    "version": 1,
                    "api_format": "openai",
                    "protocol": "chat-completion",
                },
            },
        )
        AgentModelService(session, settings).patch_inputs(
            model_id,
            {
                "row_version": str(updated.row_version),
                "image": True,
                "audio": False,
                "context_window_tokens": 32768,
            },
        )
        AssistantSettingsService(session, settings).update_settings(
            {
                "custom_instructions": None,
                "settings_version": "0",
            }
        )
    storage = MinioStorage(settings)
    scene_id = request.node.name.replace("[", "-").replace("]", "") + "-" + uuid4().hex[:10]
    objects = NamespaceStorage(storage, "acceptance/agent-runtime/" + scene_id + "/")
    case = LiveCase(
        factory,
        settings,
        model_id,
        root,
        model,
        storage,
        objects,
        Path(os.environ.get("AGENT_LIVE_REPORT_DIR", "../.runtime/agent-runtime-live")),
        scene_id,
        RunLedgerObservation(factory, settings),
        secret,
        policy,
    )
    try:
        yield case
    finally:
        cleanup = []
        for run_id in case.runs:
            with factory.begin() as session:
                run = session.get(AgentRun, run_id)
                if run.status not in {"succeeded", "failed", "cancelled"}:
                    run.cancel_requested = 1
                    run.status, run.finished_at = "cancelled", utcnow()
                run.lease_token, run.lease_until = None, None
            cleanup.append(cleanup_run(factory, settings, run_id))
        case.facts["sandbox_cleanup"] = cleanup
        try:
            for bucket in (
                settings.minio_image_bucket,
                settings.minio_video_bucket,
                settings.minio_audio_bucket,
            ):
                for item in storage.iter_objects(bucket, objects.prefix, include_versions=True):
                    storage.remove(bucket, item.object_name, version_id=item.version_id)
            case.facts["minio_namespace_cleaned"] = True
        finally:
            storage.close()
            case.report()
        assert all(cleanup), "live沙箱资源尚未清理"
