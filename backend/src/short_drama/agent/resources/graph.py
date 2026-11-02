"""Freeze media references at the tool boundary; hydrate only the next request's tool group."""

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langgraph.types import Command

from short_drama.agent.context.business_scope import assistant_invocation_locked
from short_drama.agent.gateway.deepseek import with_reasoning_content
from short_drama.agent.gateway.inputs import select_protocol
from short_drama.agent.resources.catalog import attachments_locked
from short_drama.agent.resources.contracts import HydrationIntent, ResourceRef
from short_drama.agent.resources.resolver import (
    HydratedResource,
    ResourceResolver,
    image_block,
    request_template,
)
from short_drama.agent.skill.contracts import ReadSkillResource
from short_drama.agent.skill.runtime import resource_manifest_locked
from short_drama.agent.tools.runtime import execution_locked
from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.schemas.assistant.operations.image import ReadImageContent
from short_drama.service.assistant.operations.image import authorized_business_image_locked


class GraphResources:
    def __init__(self, context, snapshot, resolver_factory):
        self.context, self.protocol = context, select_protocol(snapshot)
        self.provider = snapshot.get("provider")
        self.resolver_factory = resolver_factory
        self.model_state = ContextVar("resource_model_state", default=None)
        self.model_group = ContextVar("resource_model_group", default=None)
        self._resolver, self._storage = None, None

    def resolver(self):
        if self._resolver is None:
            if self.resolver_factory is not None:
                self._resolver = self.resolver_factory()
            else:
                from short_drama.service.media.files.storage import StorageService
                from short_drama.storage.minio import MinioStorage

                self._storage = MinioStorage(self.context.settings)
                self._resolver = ResourceResolver(
                    StorageService(self._storage, self.context.settings), self.context.settings
                )
        return self._resolver

    def close(self):
        if self._storage is not None:
            self._storage.close()

    def prepare_result(self, operation_id, output):
        message = output.update["messages"][0] if isinstance(output, Command) else output
        try:
            result = json.loads(message.content)
        except (ValueError, TypeError):
            return output
        if not isinstance(result, dict) or result.get("error"):
            return output
        intent = result.get("hydration_intent")
        if not intent and not result.get("image_ref"):
            return output
        try:
            with self.context.factory.begin() as session:
                operation = session.get(AgentOperation, operation_id)
                call = session.get(AgentToolCall, operation.tool_call_id)
                conversation, task, run, operation = execution_locked(
                    session, self.context, call.provider_call_id
                )
                if not intent:
                    values = ReadImageContent.model_validate(operation.arguments)
                    identity = assistant_invocation_locked(
                        session, conversation, task, run, operation
                    )
                    media = authorized_business_image_locked(session, identity, values)
                    intent = HydrationIntent(
                        resource=ResourceRef.model_validate(
                            {**result["image_ref"], "uri": media.storage_locator}
                        ),
                        purpose=values.purpose,
                        detail=values.detail,
                        crop=values.crop,
                        tool_call_id=call.provider_call_id,
                        invocation_id=f"turn:{call.turn_id}",
                    )
                else:
                    intent = HydrationIntent.model_validate(intent).model_copy(
                        update={"invocation_id": f"turn:{call.turn_id}"}
                    )
            views = self.resolver().freeze(intent)
            result["hydration_intents"] = [item.model_dump(mode="json") for item in views]
        except BusinessError as error:
            result.update(
                pixels_delivered=False,
                error={
                    "code": error.code,
                    "message": error.message,
                    "details": getattr(error, "details", {}),
                },
            )
            message.status = "error"
        message.content = json.dumps(result, ensure_ascii=False)
        return output

    async def hydrate(self, messages):
        return await asyncio.to_thread(self._hydrate, messages)

    @contextmanager
    def input_scope(self, messages):
        # 候选摘要可删掉最新AI；本次计量与真正水合仍沿完整请求的稳定消息身份。
        token = self.model_group.set(self._current_group(messages))
        try:
            yield
        finally:
            self.model_group.reset(token)

    def project_input(self, messages):
        projected, resources, tool_results = self._hydrate(messages, references_only=True)
        serialized = convert_to_openai_messages(projected)
        if self.provider == "deepseek" and self.protocol == "openai_chat.v1":
            serialized = with_reasoning_content(projected, serialized)
        return request_template({"messages": serialized}, resources, tool_results)["messages"]

    def _current_group(self, messages):
        latest = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], AIMessage)
                and messages[index].additional_kwargs.get("task_context_kind") != "working_notes"
            ),
            -1,
        )
        return frozenset(
            (message.id, message.tool_call_id)
            for message in messages[latest + 1 :]
            if isinstance(message, ToolMessage)
        )

    def _hydrate(self, messages, *, references_only=False):
        messages = deepcopy(messages)
        group = self.model_group.get()
        if group is None:
            group = self._current_group(messages)
        resources, delivered = [], []
        for message in messages:
            delivered.append(message)
            if (
                not isinstance(message, ToolMessage)
                or (message.id, message.tool_call_id) not in group
                or not isinstance(message.content, str)
            ):
                continue
            try:
                result = json.loads(message.content)
            except ValueError:
                continue
            if not isinstance(result, dict) or not result.get("hydration_intents"):
                continue
            intents = [
                HydrationIntent.model_validate(value) for value in result["hydration_intents"]
            ]
            if references_only:
                # 仅计量已冻结引用；真正发送仍须实时验权并校验存储字节。
                hydrated = [
                    HydratedResource(
                        intent.resource,
                        intent.resource.uri,
                        intent.resource.metadata["mime_type"],
                        "tool_result",
                        "ephemeral",
                    )
                    for intent in intents
                ]
            else:
                self._authorize(message, result)
                hydrated = [
                    self.resolver().hydrate(intent, protocol=self.protocol) for intent in intents
                ]
            resources.extend(hydrated)
            if sum(item.reference.metadata["byte_size"] for item in resources) > 4 * 1024**2:
                raise WorkflowError("agent_resource_too_large", "本次媒体总量超过 4 MiB", 422)
            blocks = [image_block(item, self.protocol) for item in hydrated]
            if self.protocol in {"claude_messages.v1", "openai_responses.v1"}:
                message.content = [{"type": "text", "text": message.content}, *blocks]
            else:
                delivered.append(
                    HumanMessage(
                        content=[
                            {"type": "text", "text": f"工具 {message.tool_call_id} 请求的媒体："},
                            *blocks,
                        ]
                    )
                )
        return delivered, resources, {}

    def _authorize(self, message, result):
        with self.context.factory.begin() as session:
            conversation, task, run, operation = execution_locked(
                session, self.context, message.tool_call_id
            )
            if message.name == "workbench__read_image_content":
                values = ReadImageContent.model_validate(operation.arguments)
                identity = assistant_invocation_locked(session, conversation, task, run, operation)
                authorized_business_image_locked(session, identity, values)
            elif message.name == "runtime__read_skill_resource":
                resource_manifest_locked(
                    session,
                    conversation,
                    task,
                    operation,
                    ReadSkillResource.model_validate(operation.arguments),
                    state=self.model_state.get(),
                )
            elif result["resource_ref"]["ref_id"] not in attachments_locked(
                session, conversation, task
            ):
                raise WorkflowError("agent_resource_unavailable", "资料已不在当前任务范围", 409)
