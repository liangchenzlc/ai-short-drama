"""Source-aware initial context and incremental inputs for the framework graph."""

import hashlib
import json
import logging
from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from sqlalchemy import select

from short_drama.agent.context.delegation import delegated_inputs_locked
from short_drama.agent.context.subjects import eligible_snapshot, subject_memory_keys
from short_drama.agent.context.working_set import eligible_scope_keys
from short_drama.agent.context.working_set import projection as working_notes
from short_drama.agent.core.access import LeaseLost, lock_run, may_decide
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.memory.context import (
    history_projection_locked,
    memory_capacity_error,
    summary_projection_locked,
)
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.resources.catalog import catalog_locked as resource_catalog
from short_drama.agent.skill.contracts import MAX_ACTIVATIONS
from short_drama.agent.skill.progressive import catalog_locked
from short_drama.agent.task.queries import task_for_run
from short_drama.agent.task.sources import result_source_ids, source_projection
from short_drama.agent.task.state import pending_task_locked
from short_drama.agent.tools.registry import tool_catalog
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.dao.assistant.execution.scope import AgentExecutionScopeDAO
from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.tasks.task import AgentTaskInput
from short_drama.service.assistant.memory.memory_store import (
    checked_scope,
    memory_capture_generation,
    memory_catalog_locked,
)
from short_drama.service.assistant.tasks.policy import generation_policy_locked

logger = logging.getLogger(__name__)


def scope_context(context, state, namespace):
    with context.factory.begin() as session:
        rows = lock_run(session, context.run_id)
        if rows is None:
            raise NotFound("执行范围不存在")
        project, conversation, run = rows
        if (
            conversation.owner_user_id != context.owner_id
            or run.task_id != context.task_id
            or not owns_execution(run, context.fence)
            or not may_decide(session, project, conversation, run)
            or run.cancel_requested
        ):
            raise LeaseLost()
        task = task_for_run(session, run)
        task_view = pending_task_locked(session, task)
        scope = AgentExecutionScopeDAO(
            session,
            owner_id=context.owner_id,
            run_id=run.id,
        ).get(scope_id=context.scope_id)
        if scope is None:
            raise Conflict("检查点命名空间没有关联到当前执行范围")
        if state.get("scope_id", str(scope.id)) != str(scope.id):
            raise Conflict("检查点状态不属于当前执行范围")
        root = scope.parent_scope_id is None
        if root != (scope.id == context.root_scope_id) or not root and not namespace:
            raise Conflict("子执行范围缺少独立检查点关联")
        restricted, _ = restricted_messages(session, conversation.id)
        task_view.context_snapshot = eligible_snapshot(task_view.context_snapshot, restricted)
        epoch = run.checkpoint.get("memory_epoch", 0)
        invalidated = state.get("memory_epoch", 0) != epoch
        if invalidated:
            # 遗忘会撤回包含旧工具正文的摘要及后续推导；从合格输入和业务收据重建。
            state = {
                **state,
                "messages": [],
                "applied_input_revision": 0,
                "context_initialized": False,
            }
        delegated = delegated_inputs_locked(session, run.id, scope.id)
        snapshot = (
            delegated.get("context_snapshot")
            if delegated is not None
            else task_view.context_snapshot
        )
        scope_keys = eligible_scope_keys(session, context.owner_id, snapshot, restricted)
        update = _eligible_sources(state, restricted, scope.id, scope_keys)
        update["memory_epoch"] = epoch
        update["scope_id"] = str(scope.id)
        policy = generation_policy_locked(session, context.owner_id)
        update["permitted_tool_names"] = sorted(
            item["name"]
            for item in tool_catalog(
                task_view,
                media_enabled=policy.media_generation_enabled,
                workbench_enabled=policy.workbench_enabled,
                memory_enabled=policy.long_term_memory_enabled,
            )
            if item["name"] in context.permitted_tool_names
        )
        _eligible_skill_tools(task_view, state, update)
        if not root:
            _restore_skill_bodies(session, context, state, update, restricted)
            _restore_working_notes(
                session, task_view, run, scope.id, state, update, restricted, scope_keys
            )
            sources = delegated.get("task_sources", {})
            if delegated.get("memory_epoch", 0) != epoch:
                sources = {
                    "content_withheld": True,
                    "reason": "委派来源已撤回，请向父任务交付当前可确认的状态。",
                }
            answers = _user_source_projection(session, task, run, scope, 0, restricted)
            update["messages"].append(
                HumanMessage(
                    content=json.dumps(
                        {
                            "task_delegation_sources": sources,
                            "task_user_sources": answers,
                            "current_skill_activations": _current_skill_activations(
                                task.id, scope.id, {**state, **update}, restricted
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    id=f"delegation-sources:{scope.id}",
                    additional_kwargs={
                        "task_context_pinned": True,
                        "source_message_ids": [item["id"] for item in answers["items"]],
                    },
                )
            )
            if invalidated:
                update["messages"].insert(0, RemoveMessage(id=REMOVE_ALL_MESSAGES))
            return update
        revision = state.get("applied_input_revision", 0)
        inputs = session.execute(
            select(AgentTaskInput, AgentMessage)
            .join(AgentMessage, AgentMessage.id == AgentTaskInput.message_id)
            .where(AgentTaskInput.task_id == task.id, AgentTaskInput.input_revision > revision)
            .order_by(AgentTaskInput.input_revision)
        ).all()
        selected = deepcopy(state.get("selected_skills", task.selected_skills))
        excluded = set(state.get("excluded_skill_ids", []))
        for item, message in inputs:
            if "selected_skills" in item.payload:
                replacements = item.payload["selected_skills"]
                excluded.update(
                    row["id"]
                    for row in selected
                    if row["id"] not in {entry["id"] for entry in replacements}
                )
                excluded.difference_update(row["id"] for row in replacements)
                selected = deepcopy(replacements)
            if message.id not in restricted:
                update["messages"].append(
                    HumanMessage(
                        content=message.content,
                        id=f"task-input:{item.id}",
                        additional_kwargs={"source_message_ids": [str(message.id)]},
                    )
                )
            revision = item.input_revision
        update.update(
            applied_input_revision=revision,
            selected_skills=selected,
            excluded_skill_ids=sorted(excluded),
        )
        for activation in update["skill_activations"]:
            if activation.get("skill", {}).get("id") in excluded:
                activation["active"] = False
                activation["issue"] = "用户已移除此方法"
        update = _revoke_messages(state, update)
        if not state.get("context_initialized"):
            _initial_messages(session, conversation, task_view, run, state, update, inputs)
            update["context_initialized"] = True
        _restore_skill_bodies(session, context, state, update, restricted)
        _restore_working_notes(
            session, task_view, run, scope.id, state, update, restricted, scope_keys
        )
        _restore_user_sources(
            session, task, revision, restricted, state, update, run=run, scope=scope
        )
        if invalidated:
            update["messages"].insert(0, RemoveMessage(id=REMOVE_ALL_MESSAGES))
        return update


def _restore_working_notes(session, task, run, scope_id, state, update, restricted, scope_keys):
    notes = working_notes(session, task, run, scope_id, {**state, **update}, restricted, scope_keys)
    update["messages"].append(
        AIMessage(
            content=json.dumps({"task_working_notes": notes}, ensure_ascii=False),
            id=f"task-notes:{task.id}:{scope_id}",
            additional_kwargs={"task_context_pinned": True, "task_context_kind": "working_notes"},
        )
    )


def _restore_user_sources(session, task, revision, restricted, state, update, *, run, scope):
    sources = _user_source_projection(session, task, run, scope, revision, restricted)
    current_run = {"run_id": str(run.id)}
    source_ids = {item["id"] for item in sources["items"]}
    if run.trigger_message_id in restricted:
        current_run["trigger_source_withheld"] = True
    else:
        trigger = session.get(AgentMessage, run.trigger_message_id)
        current_run.update(trigger_message_id=str(trigger.id), trigger_seq=trigger.seq)
        source_ids.add(str(trigger.id))
    identifier = f"task-sources:{task.id}:{revision}"
    update["messages"].extend(
        RemoveMessage(id=message.id)
        for message in state["messages"]
        if message.id
        and message.id.startswith(f"task-sources:{task.id}")
        and message.id != identifier
    )
    update["messages"].append(
        HumanMessage(
            content=json.dumps(
                {
                    "task_user_sources": sources,
                    "current_run": current_run,
                    "current_skill_activations": _current_skill_activations(
                        task.id, scope.id, {**state, **update}, restricted
                    ),
                },
                ensure_ascii=False,
            ),
            id=identifier,
            additional_kwargs={
                "task_context_pinned": True,
                "source_message_ids": sorted(source_ids),
            },
        )
    )


def _user_source_projection(session, task, run, scope, revision, restricted):
    rows = []
    if scope.parent_scope_id is None:
        rows = session.execute(
            select(AgentMessage.id, AgentMessage.seq, AgentMessage.content)
            .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
            .where(AgentTaskInput.task_id == task.id, AgentTaskInput.input_revision <= revision)
        ).all()
    rows.extend(
        AgentTranscriptDAO(session).scope_answer_messages(task.id, run.id, scope.id, restricted)
    )
    return source_projection(
        [
            {"id": str(row.id), "seq": row.seq, "content": row.content}
            for row in sorted(rows, key=lambda row: row.seq)
        ],
        restricted,
    )


def _current_skill_activations(task_id, scope_id, state, restricted):
    excluded = set(state.get("excluded_skill_ids", []))
    invalid = {str(identifier) for identifier in restricted}
    records = [
        item
        for item in state.get("skill_activations", [])
        if item.get("task_id") == str(task_id)
        and item.get("scope_id") == str(scope_id)
        and item.get("source_message_id") not in invalid
        and item["skill"]["id"] not in excluded
    ]
    return [
        {
            "activation_id": item["id"],
            "skill_id": item["skill"]["id"],
            "content_version": item["skill"]["content_version"],
            "body_operation_id": item["body_operation_id"],
            "active": item["active"],
            "issue": item.get("issue"),
        }
        for item in records[-MAX_ACTIVATIONS:]
    ]


def _initial_messages(session, conversation, task, run, state, update, inputs):
    keys = subject_memory_keys(task.context_snapshot)
    scope = checked_scope(session, conversation.owner_user_id)
    trigger = session.get(AgentMessage, run.trigger_message_id)
    summary, facts = summary_projection_locked(session, conversation, task, keys)
    if summary is not None and summary.to_seq >= trigger.seq:
        # 历史重答只可使用触发消息之前的来源，不能带入后续会话摘要。
        summary, facts = None, []
    page = history_projection_locked(
        session,
        conversation,
        task,
        summary.to_seq + 1 if summary else 1,
        trigger.seq - 1,
        limit=500,
        allowed_subject_keys=keys,
    )
    if page["next_seq"] is not None:
        raise AgentGatewayError(memory_capacity_error(session, conversation))
    input_ids = {str(message.id): f"task-input:{item.id}" for item, message in inputs}
    history = [
        (HumanMessage if item["role"] == "user" else AIMessage)(
            content=item["content"],
            id=input_ids.get(str(item["id"]), f"history:{item['id']}"),
            additional_kwargs={"source_message_ids": [item["id"]]},
        )
        for item in page["items"]
        if not item["source_restricted"]
    ]
    if summary is not None:
        history.insert(
            0,
            HumanMessage(
                content=json.dumps(
                    {
                        "conversation_summary": {
                            "id": str(summary.id),
                            "covers_until_seq": summary.to_seq,
                            "facts": facts,
                        }
                    },
                    ensure_ascii=False,
                ),
                id=f"conversation-summary:{summary.id}",
                additional_kwargs={
                    "task_context_pinned": True,
                    "source_summary_ids": [str(summary.id)],
                    "source_message_ids": sorted(
                        {value for fact in facts for value in fact["source_message_ids"]}
                    ),
                    "subject_keys": sorted(
                        {key for fact in facts for key in fact.get("subject_keys", [])}
                    ),
                },
            ),
        )
    metadata = {
        "task_id": str(task.id),
        "execution_target": task.execution_target,
        "subjects": task.context_snapshot,
        "resources": resource_catalog(session, conversation, task),
        "memory_catalog": memory_catalog_locked(session, scope)
        if memory_capture_generation(session, conversation.owner_user_id) is not None
        else None,
        "skill_catalog": catalog_locked(
            session,
            conversation.owner_user_id,
            task,
            state={**state, **update},
            run=run,
        ),
    }
    represented = {message.id for message in history}
    update["messages"] = [
        *history,
        HumanMessage(
            content=json.dumps(metadata, ensure_ascii=False),
            id=f"task-context:{task.id}",
        ),
        *[message for message in update["messages"] if message.id not in represented],
    ]


def _eligible_sources(state, restricted, scope_id, scope_keys):
    records = deepcopy(state.get("skill_activations", []))
    for item in records:
        if item.get("active") and (
            item.get("scope_id") != str(scope_id)
            or item.get("source_message_id") in {str(value) for value in restricted}
        ):
            item["active"] = False
            item["issue"] = "方法来源已不在当前范围"
    update = {"messages": [], "skill_activations": records}
    invalid = {str(identifier) for identifier in restricted}
    for message in state["messages"]:
        if invalid.intersection(message.additional_kwargs.get("source_message_ids", [])) or not set(
            message.additional_kwargs.get("subject_keys", [])
        ).issubset(scope_keys):
            update["messages"].append(RemoveMessage(id=message.id))
    return _revoke_messages(state, update)


def _revoke_messages(state, update):
    inactive = {item["id"] for item in update["skill_activations"] if not item.get("active")}
    active = {item["id"] for item in update["skill_activations"] if item.get("active")}
    remove = {message.id for message in update["messages"] if isinstance(message, RemoveMessage)}
    calls = set()
    for message in state["messages"]:
        if message.id in remove and isinstance(message, ToolMessage):
            calls.add(message.tool_call_id)
        sources = set(message.additional_kwargs.get("skill_activation_ids", []))
        if sources & inactive or sources - active:
            remove.add(message.id)
            if isinstance(message, ToolMessage):
                calls.add(message.tool_call_id)
    # 工具调用组作为整体撤回，不能把没有 call 的 ToolMessage 留给供应商。
    for message in state["messages"]:
        if isinstance(message, AIMessage) and any(
            call["id"] in calls for call in message.tool_calls
        ):
            remove.add(message.id)
            calls.update(call["id"] for call in message.tool_calls)
    for message in state["messages"]:
        if isinstance(message, ToolMessage) and message.tool_call_id in calls:
            remove.add(message.id)
    update["messages"] = [
        *[message for message in update["messages"] if not isinstance(message, RemoveMessage)],
        *[RemoveMessage(id=identifier) for identifier in sorted(remove)],
    ]
    return update


def _eligible_skill_tools(task, state, update):
    mode = (task.execution_target or {}).get("mode")
    permitted = set(update["permitted_tool_names"])
    for item in update["skill_activations"]:
        if not item.get("active"):
            continue
        skill = item["skill"]
        missing = set(skill.get("required_tools", [])) - permitted
        if mode and mode not in skill.get("modes", []):
            item["active"], item["issue"] = False, "方法不适用于当前任务模式，请重新选择方法"
        elif missing:
            item["active"], item["issue"] = (
                False,
                ("当前政策或范围不再允许方法所需工具：" + "、".join(sorted(missing))),
            )
    _revoke_messages(state, update)


def _require_skill_body(context, item, result):
    body = result.get("instructions")
    expected = item["reference"]["metadata"]["body_checksum"]
    if (
        not isinstance(body, str)
        or not body
        or hashlib.sha256(body.encode()).hexdigest() != expected
    ):
        logger.error(
            "Skill context unavailable operation_id=%s scope_id=%s reason=%s",
            item["body_operation_id"],
            context.scope_id,
            "missing" if not body else "invalid_body",
        )
        raise AgentGatewayError("agent_skill_context_missing")


def _restore_skill_bodies(session, context, state, update, restricted):
    removed = {item.id for item in update["messages"] if isinstance(item, RemoveMessage)}
    messages = [
        item for item in [*state["messages"], *update["messages"]] if item.id not in removed
    ]
    active = [item for item in update["skill_activations"] if item.get("active")]
    by_operation = {item["body_operation_id"]: item for item in active}
    present = set()
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == "runtime__load_skill":
            result = json.loads(message.content)
            if result.get("instructions") and result.get("body_operation_id") in by_operation:
                _require_skill_body(context, by_operation[result["body_operation_id"]], result)
                present.add(result.get("body_operation_id"))
    for item in active:
        identifier = item["body_operation_id"]
        if identifier in present:
            continue
        group = AgentTranscriptDAO(session).scope_skill_group(
            context.task_id,
            context.run_id,
            context.scope_id,
            int(identifier),
        )
        archived = next(
            (
                operation.result or {}
                for _call, operation in group
                if str(operation.id) == identifier
            ),
            {},
        )
        _require_skill_body(context, item, archived)
        update["messages"].append(
            AIMessage(
                content="",
                id=f"operation-call:{group[0][0].turn_id}",
                tool_calls=[
                    {
                        "id": call.provider_call_id,
                        "name": call.tool_name,
                        "args": call.arguments.get("parsed", call.arguments),
                    }
                    for call, _ in group
                ],
            )
        )
        for call, operation in group:
            result = operation.result or {"error": operation.error}
            records = [
                record
                for record in update["skill_activations"]
                if record.get("body_operation_id") == str(operation.id)
                or record["id"] == result.get("activation_id")
            ]
            eligible = [record for record in records if record.get("active")]
            if eligible and result.get("instructions"):
                _require_skill_body(context, eligible[0], result)
            original = result.get("activation_record")
            origins = eligible or records or ([original] if original else [])
            metadata = {
                "source_message_ids": sorted({record["source_message_id"] for record in origins}),
                "skill_activation_ids": [record["id"] for record in origins],
            }
            answer_sources = result_source_ids(result)
            if answer_sources:
                metadata = {"source_message_ids": answer_sources, "skill_activation_ids": []}
                if result.get("answer_message_id"):
                    metadata["answer_message_id"] = result["answer_message_id"]
                if set(answer_sources).intersection(str(value) for value in restricted):
                    result = {
                        "status": operation.status,
                        "content_withheld": True,
                        "answer_message_id": result.get("answer_message_id"),
                    }
            if not eligible and (records or call.tool_name == "runtime__load_skill"):
                # 同组关系保留真实终态；兄弟仍活动不能赋予已撤回正文新的来源资格。
                result = {
                    "status": operation.status,
                    "activation_id": result.get("activation_id"),
                    "body_operation_id": result.get("body_operation_id"),
                    "content_withheld": True,
                }
            update["messages"].append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    id=f"operation-result:{operation.id}",
                    name=call.tool_name,
                    tool_call_id=call.provider_call_id,
                    additional_kwargs=metadata,
                )
            )
            if eligible and result.get("instructions"):
                present.add(str(operation.id))
        present.add(identifier)
    update["skill_body_operations"] = sorted(item["body_operation_id"] for item in active)
