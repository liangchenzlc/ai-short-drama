"""One disposable acceptance Worker; credentials enter only through its stdin pipe."""

import base64
import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

from opensandbox import SandboxSync
from opensandbox.models.sandboxes import NetworkPolicy
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from short_drama.agent.resources.resolver import ResourceResolver
from short_drama.agent.runtime import executor
from short_drama.core.config import Settings
from short_drama.db.session import AppSession, configure_mysql
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.service.media.files.storage import StorageService
from short_drama.storage.minio import MinioStorage
from tests.integration.standard.assistant.test_global_assistant_tasks import task_service
from tests.support.agent_runtime_live import (
    ControlledStorage,
    NamespaceStorage,
    acceptance_api,
    validate_live_target,
)
from tests.support.assistant_runtime import WorkerLost


def install_boundary(stack, factory, settings, accepted_run_id, fault, facts):
    fired = []

    def once(boundary):
        if fired:
            return False
        fired.append(boundary)
        facts["boundary"] = boundary
        return True

    def stop():
        with factory() as session:
            task_id = session.get(AgentRun, accepted_run_id).task_id
            session.rollback()
            task_service(session, settings).control_task(
                task_id, {"kind": "stop"}, "live-boundary-stop"
            )
        with factory() as session:
            run = session.get(AgentRun, accepted_run_id)
            facts["decision_calls_at_stop"] = run.usage["decision_calls"]
            facts["operation_ids_at_stop"] = [
                str(identifier)
                for identifier in session.scalars(
                    select(AgentOperation.id).where(AgentOperation.run_id == run.id)
                )
            ]

    if fault in {"model_committed", "child_model_committed", "model_identity"}:
        from langchain_core.messages import message_to_dict

        from short_drama.agent.runtime.middleware import ExecutionMiddleware
        from short_drama.service.assistant.execution.tools import AgentToolExecutionService
        from short_drama.utils.json_digest import digest

        original_wrap = ExecutionMiddleware.awrap_model_call
        original_result = AgentToolExecutionService.model_result

        async def observe(middleware, request, handler):
            info = request.runtime.execution_info
            row = {
                "identity": {
                    "thread_id": info.thread_id,
                    "checkpoint_id": info.checkpoint_id,
                    "checkpoint_ns": info.checkpoint_ns,
                    "task_id": info.task_id,
                },
                "scope_id": str(request.runtime.context.scope_id),
                "content_hash": digest(
                    [message_to_dict(item) for item in request.state["messages"]]
                ),
            }
            facts.setdefault("model_tasks", []).append(row)
            return await original_wrap(middleware, request, handler)

        def result(service, signature, message):
            original_result(service, signature, message)
            child = service.context.scope_id != service.context.root_scope_id
            if fault != "model_identity" and child == (fault == "child_model_committed"):
                if once(fault):
                    raise WorkerLost("acceptance worker lost after model receipt before checkpoint")

        stack.enter_context(patch.object(ExecutionMiddleware, "awrap_model_call", observe))
        stack.enter_context(patch.object(AgentToolExecutionService, "model_result", result))
    elif fault in {"business_committed", "stop_tool"}:
        from short_drama.service.assistant.execution.tools import AgentToolExecutionService

        original = AgentToolExecutionService.finish_tool

        def finish(service, operation_id, output):
            result = original(service, operation_id, output)
            with factory() as session:
                operation = session.get(AgentOperation, operation_id)
                selected = operation.tool_name == "workbench__apply_standard_patch"
                if selected and fault == "business_committed":
                    receipt = (
                        session.get(BusinessToolInvocation, operation.business_invocation_id)
                        if operation.business_invocation_id is not None
                        else None
                    )
                    selected = (
                        operation.status == "succeeded"
                        and receipt is not None
                        and receipt.status == "succeeded"
                    )
            if selected and once(fault):
                if fault == "stop_tool":
                    stop()
                else:
                    raise WorkerLost("acceptance worker lost after committed business tool")
            return result

        stack.enter_context(patch.object(AgentToolExecutionService, "finish_tool", finish))
    elif fault == "interrupt_registered":
        from short_drama.agent.runtime.waiting import RuntimeQuestions

        original = RuntimeQuestions.register

        def register(service, *args, **kwargs):
            result = original(service, *args, **kwargs)
            if once(fault):
                raise WorkerLost("acceptance worker lost before interrupt binding")
            return result

        stack.enter_context(patch.object(RuntimeQuestions, "register", register))
    elif fault == "stop_stream":
        from short_drama.agent.runtime.events import AgentEventAdapter

        original = AgentEventAdapter.model_content_event

        def content(adapter, event):
            result = original(adapter, event)
            if event.get("source") == "assistant" and event.get("channel") == "text":
                if event.get("delta") and once(fault):
                    stop()
            return result

        stack.enter_context(patch.object(AgentEventAdapter, "model_content_event", content))
    elif fault == "stop_code":
        from short_drama.agent.sandbox.backend import CommandBackend

        original = CommandBackend._event

        def event(backend, kind, payload, call):
            result = original(backend, kind, payload, call)
            if kind == "sandbox.started" and once(fault):
                stop()
            return result

        stack.enter_context(patch.object(CommandBackend, "_event", event))
    elif fault == "close_policy_before_generation":
        from short_drama.service.assistant.execution.tools import AgentToolExecutionService
        from short_drama.service.assistant.settings.preferences import AssistantSettingsService

        original = AgentToolExecutionService.start_tool

        def start(service, call, state):
            result = original(service, call, state)
            if call["name"] == "workbench__submit_generation_batch" and once(fault):
                with factory() as session:
                    task_service(session, settings)
                    preferences = AssistantSettingsService(session, settings)
                    saved = preferences.update_settings(
                        {
                            "settings_version": str(preferences.get_settings().settings_version),
                            "media_generation_enabled": False,
                        }
                    )
                    facts["closed_settings_version"] = str(saved.settings_version)
            return result

        stack.enter_context(patch.object(AgentToolExecutionService, "start_tool", start))


def controlled_transport(replies):
    from tests.support.assistant_runtime import RuntimeTransport, tool_result

    def resolve(value, body):
        if isinstance(value, dict) and set(value) == {"$result"}:
            call_id, *keys = value["$result"]
            result = tool_result(body, call_id)
            for key in keys:
                result = result[key]
            return result
        if isinstance(value, dict):
            return {key: resolve(child, body) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child, body) for child in value]
        return value

    if isinstance(replies, list):
        iterator = iter(replies)
        return RuntimeTransport([lambda body: resolve(next(iterator), body)] * len(replies))
    routes = {name: iter(items) for name, items in replies["routes"].items()}

    def route(body):
        name = next(
            (
                message["content"].split("；", 1)[0].removeprefix("子任务:")
                for message in body["messages"]
                if message["role"] == "user"
                and isinstance(message.get("content"), str)
                and message["content"].startswith("子任务:")
            ),
            "root",
        )
        return resolve(next(routes[name]), body)

    return RuntimeTransport([route] * sum(len(items) for items in replies["routes"].values()))


def deliver(payload):
    root, model, secret = payload["root"], payload["model"], payload["api_key"]
    validate_live_target(root, model)
    if (
        payload.get("controlled_replies") is None
        and os.environ.get("RUN_AGENT_RUNTIME_LIVE") != "1"
    ):
        raise RuntimeError("真实文本验收尚未启用")
    database = make_url(payload["database_url"])
    assert database.host in {"127.0.0.1", "localhost"} and database.port == 13307
    assert (database.database or "").startswith("short_drama_")
    assert (database.database or "").endswith("_test")
    settings = Settings(_env_file=None, **payload["settings"])
    engine = configure_mysql(create_engine(database, hide_parameters=True, pool_pre_ping=True))
    factory = sessionmaker(engine, class_=AppSession, expire_on_commit=False, autoflush=False)
    storage = MinioStorage(settings) if settings.minio_endpoint else None
    if payload.get("controlled_objects") is not None:
        assert os.environ.get("RUN_AGENT_RUNTIME_LIVE") != "1"
        storage = ControlledStorage()
        storage.objects = {
            (bucket, key): (base64.b64decode(data), mime)
            for bucket, key, data, mime in payload["controlled_objects"]
        }
    objects = NamespaceStorage(storage, payload["prefix"]) if storage else None
    facts = {"pid": os.getpid(), "fault": payload.get("fault")}

    def credential(snapshot, _key):
        assert snapshot["model_key"] == model and snapshot["base_url"] == root + "/v1"
        return secret

    create = SandboxSync.create

    def restricted_create(*args, **kwargs):
        kwargs["network_policy"] = NetworkPolicy.model_validate(payload["network_policy"])
        return create(*args, **kwargs)

    transport = None
    if payload.get("controlled_replies") is not None:
        assert os.environ.get("RUN_AGENT_RUNTIME_LIVE") != "1"
        transport = controlled_transport(payload["controlled_replies"])
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(executor, "model_credentials", credential))
            stack.enter_context(patch.object(SandboxSync, "create", restricted_create))
            if payload.get("controlled_objects") is not None:
                from short_drama.service.skills.catalog.skill import AgentSkillService

                stack.enter_context(
                    patch(
                        "short_drama.agent.skill.runtime.read_resource_bytes",
                        lambda config, manifest: AgentSkillService(
                            None, config, objects
                        )._resource_bytes(manifest),
                    )
                )
            install_boundary(
                stack, factory, settings, payload["run_id"], payload.get("fault"), facts
            )
            if payload.get("answer"):
                from short_drama.service.assistant.operations.questions import QuestionService

                original = QuestionService.answer

                def answer(service, *args, **kwargs):
                    result = original(service, *args, **kwargs)
                    if payload.get("fault") == "answer_committed":
                        facts["boundary"], facts["crash_injected"] = "answer_committed", True
                        raise RuntimeError("acceptance answer process lost after committed answer")
                    return result

                stack.enter_context(patch.object(QuestionService, "answer", answer))
                with acceptance_api(
                    factory, settings, objects, raise_server_exceptions=False
                ) as client:
                    response = client.post(
                        f"/assistant/operations/{payload['answer']['operation_id']}/answer",
                        json=payload["answer"]["payload"],
                        headers={"idempotency-key": "live-answer"},
                    )
                facts["answer_http_status"] = response.status_code
            else:
                try:
                    executor.AgentRuntime(
                        factory,
                        settings,
                        transport=transport,
                        resolver_factory=(
                            lambda: ResourceResolver(StorageService(objects, settings), settings)
                        )
                        if objects
                        else None,
                    ).execute(payload["run_id"], payload["message_version"])
                except WorkerLost:
                    if facts.get("boundary") not in {
                        "business_committed",
                        "interrupt_registered",
                        "model_committed",
                        "child_model_committed",
                    }:
                        raise
                    facts["crash_injected"] = True
        if transport is not None:
            facts["controlled_requests"] = len(transport.main_requests)
        with factory() as session:
            run = session.get(AgentRun, payload["run_id"])
            facts["status"] = run.status
        return facts
    finally:
        if storage and hasattr(storage, "close"):
            storage.close()
        engine.dispose()


if __name__ == "__main__":
    data = json.load(sys.stdin)
    result = deliver(data)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
