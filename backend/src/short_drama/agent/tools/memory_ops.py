"""Memory writes derive authority from applied user messages, never from tool arguments."""

import re
from dataclasses import dataclass

from sqlalchemy import or_

from short_drama.agent.tools.contracts import MEMORY_WRITE_TOOLS as MEMORY_WRITE_TOOLS
from short_drama.dao.assistant.memory.memory import AgentMemoryDAO
from short_drama.domain.assistant.memory.memory import AgentMemoryItem
from short_drama.schemas.assistant.memory.memory import normalize_memory_key
from short_drama.service.assistant.memory.memory_store import checked_scope
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
