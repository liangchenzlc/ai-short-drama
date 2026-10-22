"""Memory writes derive authority from applied user messages, never from tool arguments."""

import re
from dataclasses import dataclass

from sqlalchemy import or_, select

from short_drama.agent.memory_sources import restricted_messages
from short_drama.agent.tool_parameters import MEMORY_WRITE_TOOLS as MEMORY_WRITE_TOOLS
from short_drama.agent.tool_parameters import ChangeMemory as ChangeMemory
from short_drama.agent.tool_parameters import MemoryRequest as MemoryRequest
from short_drama.agent.tool_parameters import RememberMemory as RememberMemory
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.dao.agent_memory_dao import AgentMemoryDAO
from short_drama.domain.agent import AgentMessage
from short_drama.domain.agent_memory import AgentMemoryItem
from short_drama.domain.agent_task import AgentOperation, AgentTaskInput
from short_drama.schemas.agent_memory import MemoryCreate, normalize_memory_key
from short_drama.service.agent_memory_store import (
    apply_memory_command_locked,
    blocked_extraction,
    checked_scope,
    create_memory_locked,
    source_rows,
)
from short_drama.utils.json_digest import digest


@dataclass(frozen=True)
class MemoryDirective:
    action: str
    scope_type: str | None
    content: str | None = None
    target: str | None = None


def _scope_prefix(text):
    match = re.match(
        r"^(?:(?:在|到)\s*)?(账号|全局|当前会话|本会话|当前作品|本作品)"
        r"(?:(?:偏好|记忆|设定)(?:里|中|的)?|(?:里|中|的)|(?=[：:\s]))\s*[：:]?\s*",
        text,
    )
    if not match:
        return None, text
    label = match[1]
    scope = (
        "user" if label in {"账号", "全局"} else "project" if "作品" in label else "conversation"
    )
    return scope, text[match.end() :]


def parse_memory_directive(content):
    text = content.strip()
    # Quoted examples, questions and multi-command payloads are not write grants.
    if (
        not text
        or "\x00" in text
        or text.endswith(("?", "？"))
        or re.search(
            r"\n\s*(?:请(?:帮我|你)?|帮我)?(?:重新记住|恢复记忆|记住|记下|修改记忆|更新记忆|纠正记忆|忘掉|忘记|遗忘)",
            text,
        )
    ):
        return None
    text = re.sub(r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", text, count=1)
    scope, text = _scope_prefix(text)
    match = re.match(
        r"^(重新记住|恢复记忆|记住|记下|修改记忆|更新记忆|纠正记忆|忘掉|忘记|遗忘)", text
    )
    if not match:
        return None
    action = {
        "重新记住": "restore",
        "恢复记忆": "restore",
        "记住": "remember",
        "记下": "remember",
        "修改记忆": "update",
        "更新记忆": "update",
        "纠正记忆": "update",
    }.get(match[1], "forget")
    tail = text[match.end() :].strip()
    after_scope, tail = _scope_prefix(tail)
    if scope and after_scope and scope != after_scope:
        return None
    scope = after_scope or scope
    tail = re.sub(r"^记忆\s*", "", tail, count=1).lstrip("：:,， ")
    if not tail or re.match(r"(?:是什么|是什么意思|怎么|如何|所有|全部)", tail):
        return None
    if action == "remember":
        return MemoryDirective(action, scope, content=tail) if len(tail) <= 12000 else None
    body = None
    if action == "update":
        change = re.fullmatch(
            r"([^\n]+?)(?:修改为|更新为|改为|改成|为[：:]?|[：:])\s*(.+)", tail, re.DOTALL
        )
        if not change:
            return None
        tail, body = change[1].strip(), change[2].strip()
        if not body or len(body) > 12000:
            return None
    pairs = {"“": "”", "‘": "’", '"': '"', "「": "」"}
    if tail[:1] in pairs and tail.endswith(pairs[tail[0]]):
        tail = tail[1:-1].strip()
    if (
        not tail
        or "\n" in tail
        or len(tail) > 200
        or tail in {"所有记忆", "全部记忆", "这条", "这条记忆", "为", "改为"}
    ):
        return None
    return MemoryDirective(action, scope, content=body, target=tail)


def _scope(session, conversation, task):
    return checked_scope(
        session, conversation.owner_user_id, conversation.id, task.memory_project_ids
    )


def _candidates(session, scope, directive):
    key = normalize_memory_key(directive.target)
    identities = [AgentMemoryItem.memory_key == key, AgentMemoryItem.title == directive.target]
    if key.isascii() and key.isdecimal() and 0 < int(key) < 2**64:
        identities.append(AgentMemoryItem.id == int(key))
    query = AgentMemoryDAO(session).rows(scope, active=False).where(or_(*identities))
    if directive.scope_type:
        query = query.where(AgentMemoryItem.scope_type == directive.scope_type)
    return session.scalars(query.order_by(AgentMemoryItem.id).limit(2).with_for_update()).all()


def _reference(row):
    return {
        "kind": "memory",
        "id": str(row.id),
        "name": row.title,
        "scope_type": row.scope_type,
        "content_version": str(row.content_version),
        "state": row.state,
    }


def memory_requests_locked(session, conversation, task, rows, restricted_ids):
    scope = _scope(session, conversation, task)
    result = []
    for message, _item in rows:
        directive = parse_memory_directive(message.content)
        if message.role != "user" or message.id in restricted_ids or directive is None:
            continue
        request = {
            "request_message_id": str(message.id),
            "request_hash": digest({"role": message.role, "content": message.content}),
            "action": directive.action,
            "scope_type": directive.scope_type,
        }
        if directive.action != "remember":
            request["targets"] = [_reference(row) for row in _candidates(session, scope, directive)]
        elif directive.scope_type == "project":
            request["project_ids"] = task.memory_project_ids
        result.append(request)
    return result


def execute_memory_locked(session, conversation, task, operation, values):
    scope = _scope(session, conversation, task)
    source = session.execute(
        select(AgentMessage, AgentTaskInput)
        .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
        .where(
            AgentMessage.id == values.request_message_id,
            AgentMessage.conversation_id == conversation.id,
            AgentMessage.role == "user",
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.applied_plan_revision.is_not(None),
            AgentTaskInput.input_revision <= operation.input_revision,
        )
        .with_for_update()
    ).one_or_none()
    if source is None:
        raise WorkflowError(
            "memory_request_required", "记忆操作必须引用本任务已应用的用户直接指令", 403
        )
    message, item = source
    directive = parse_memory_directive(message.content)
    if (
        directive is None
        or directive.action != MEMORY_WRITE_TOOLS[operation.tool_name][1]
        or values.request_hash != digest({"role": message.role, "content": message.content})
    ):
        raise WorkflowError(
            "memory_request_required", "指令原文与记忆操作不符，请明确要修改的记忆", 403
        )
    # Repeated model calls and later attempts return the original effect, not another write.
    prior = session.scalar(
        select(AgentOperation)
        .where(
            AgentOperation.task_id == task.id,
            AgentOperation.status == "succeeded",
            AgentOperation.tool_name.in_(MEMORY_WRITE_TOOLS),
            AgentOperation.arguments["request_message_id"].as_string() == str(message.id),
        )
        .order_by(AgentOperation.id)
        .limit(1)
        .with_for_update()
    )
    if prior is not None:
        identifier = (prior.result or {}).get("memory", {}).get("id")
        row = (
            session.scalar(
                AgentMemoryDAO(session)
                .rows(scope, active=False)
                .where(AgentMemoryItem.id == int(identifier))
            )
            if identifier
            else None
        )
        if row is None:
            raise Conflict("原记忆操作已完成，但条目已停止使用，请查看原回执")
        operation.result_refs = [_reference(row)]
        return {"memory": _reference(row), "already_applied": True, "operation_id": str(prior.id)}
    restricted, _tasks = restricted_messages(session, conversation.id)
    later = session.scalars(
        select(AgentMessage.content)
        .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
        .where(
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision > item.input_revision,
            AgentTaskInput.input_revision <= operation.input_revision,
            AgentTaskInput.applied_plan_revision.is_not(None),
        )
    ).all()
    if message.id in restricted or any(
        re.search(r"(?:不要|别|取消|无需|不再).{0,8}(?:记住|记忆|保存|记录)", text)
        for text in later
    ):
        raise Conflict("这条旧记忆指令已被纠正、遗忘或撤回，请使用新的明确指令")
    sources = source_rows(session, conversation.owner_user_id, conversation.id, [message.id])
    before = None
    if directive.action == "remember":
        memory_scope = directive.scope_type or "conversation"
        if memory_scope == "project" and len(scope.project_ids) != 1:
            raise Conflict("请先明确绑定一个作品记忆范围，再保存作品记忆")
        payload = {
            "scope_type": memory_scope,
            "kind": values.kind,
            "memory_key": values.memory_key,
            "title": values.title,
            "content": directive.content,
        }
        if memory_scope == "conversation":
            payload["conversation_id"] = str(conversation.id)
        if memory_scope == "project":
            payload["project_id"] = str(scope.project_ids[0])
        create = MemoryCreate.model_validate(payload)
        scope_key = (
            "user"
            if memory_scope == "user"
            else str(
                create.conversation_id if memory_scope == "conversation" else create.project_id
            )
        )
        if blocked_extraction(
            session,
            conversation.owner_user_id,
            memory_scope,
            scope_key,
            create.kind,
            create.memory_key,
            sources,
        ):
            raise Conflict("此记忆已遗忘，不能用旧指令或新标题恢复，请明确重新记住原条目")
        row = create_memory_locked(session, conversation.owner_user_id, create, sources=sources)
    else:
        matches = _candidates(session, scope, directive)
        if len(matches) != 1 or matches[0].id != values.memory_id:
            raise Conflict("记忆对象不唯一或与原指令不符，请明确范围和标题")
        row = matches[0]
        if row.content_version != values.content_version or row.updated_at > message.created_at:
            raise Conflict("记忆在此指令之后已变化，请核对当前版本并发送新的指令")
        before = _reference(row)
        apply_memory_command_locked(session, row, directive.action, directive.content, sources)
    session.flush()
    reference = _reference(row)
    operation.result_refs = [reference]
    operation.changes = [
        {
            "kind": "memory",
            "id": str(row.id),
            "before": before,
            "after": reference,
            "request_message_id": str(message.id),
        }
    ]
    return {"memory": reference, "already_applied": False}
