"""Server-authored delivery requirements and the publish guard.

``agent_task_requirements`` is authoritative: the executor may confirm drafts and
raise counts, it can never delete a requirement or author one without a real
quote. ``effective_state.task_contract`` stays as a compatibility projection for
frozen requests and old readers.
"""

import re

from sqlalchemy import select

from short_drama.agent.task.intake import draft_requirements
from short_drama.agent.task.policy import policy
from short_drama.agent.task.sources import WRITE_TOOLS, source_valid
from short_drama.core.exceptions import WorkflowError
from short_drama.domain.agent_task import AgentOperation, AgentTaskInput
from short_drama.domain.agent_task_requirement import AgentTaskRequirement
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id

KIND_VALUES = frozenset({"answer", "read", "write", "image", "skill", "observation", "contract"})


def load_task_requirements(session, task, *, states=("confirmed", "frozen")):
    return session.scalars(
        select(AgentTaskRequirement)
        .where(
            AgentTaskRequirement.task_id == task.id,
            AgentTaskRequirement.state.in_(states),
        )
        .order_by(AgentTaskRequirement.id)
    ).all()


def draft_rows(session, task):
    return session.scalars(
        select(AgentTaskRequirement)
        .where(
            AgentTaskRequirement.task_id == task.id,
            AgentTaskRequirement.state == "draft",
        )
        .order_by(AgentTaskRequirement.id)
    ).all()


def _insert(session, task, message_id, **values):
    row = AgentTaskRequirement(
        id=next_id(),
        task_id=task.id,
        source_message_id=message_id,
        input_revision=task.input_revision,
        attempts=0,
        quote_start=values.pop("quote_start", 0),
        quote_end=values.pop("quote_end", 0),
        definition_hash=digest(values),
        created_at=utcnow(),
        updated_at=utcnow(),
        **values,
    )
    session.add(row)
    return row


def seed_requirements_locked(session, task, message):
    """Freeze server drafts before any model call. Idempotent per message."""
    if policy(task, "contract") != "server_draft.v1":
        return 0
    if not getattr(message, "content", ""):
        return 0
    existing = session.scalar(
        select(AgentTaskRequirement.id).where(
            AgentTaskRequirement.task_id == task.id,
            AgentTaskRequirement.source_message_id == message.id,
        )
    )
    if existing is not None:
        return 0
    drafts = draft_requirements(message.content)
    for draft in drafts:
        _insert(
            session,
            task,
            message.id,
            requirement_key=f"m{message.id}-{draft.key}",
            description=draft.description,
            kind=draft.kind,
            origin="server_draft",
            state="draft",
            minimum_count=draft.minimum_count,
            artifact_kind=draft.artifact_kind,
            asset_kind=draft.asset_kind,
            quote_start=draft.quote_start,
            quote_end=draft.quote_end,
        )
    return len(drafts)


def project_contract(task, rows, *, intent=None, verified=False):
    """Keep the legacy JSON view in sync with the authoritative rows."""
    previous = task.effective_state.get("task_contract") or {}
    requirements = [
        {
            "key": row.requirement_key,
            "description": row.description,
            "kind": row.kind,
            "source_message_id": str(row.source_message_id),
            "source_quote": "",
            "minimum_count": row.minimum_count,
            "artifact_kind": row.artifact_kind,
            "asset_kind": row.asset_kind,
            "state": row.state,
            "attempts": row.attempts,
        }
        for row in rows
        if row.state in {"confirmed", "frozen"}
    ]
    task.effective_state = {
        **task.effective_state,
        "task_contract": {
            "input_revision": str(task.applied_input_revision),
            "intent": intent or previous.get("intent") or "execution",
            "requirements": requirements,
            "reviewed_evidence": previous.get("reviewed_evidence", {}),
            "verified": verified,
        },
    }


def authoritative_definitions(session, task, messages, restricted):
    """Definitions backed by real quote spans, or ``None`` for legacy-only tasks."""
    if session is None or policy(task, "contract") != "server_draft.v1":
        return None
    rows = load_task_requirements(session, task)
    if not rows or any(row.quote_end <= row.quote_start for row in rows):
        return None
    definitions = {}
    for row in rows:
        source = messages.get(str(row.source_message_id))
        if source is None or int(row.source_message_id) in restricted:
            continue
        # Requirement.source_quote is bounded; a longer sentence keeps its head.
        quote_end = min(row.quote_end, row.quote_start + 500)
        definitions[row.requirement_key] = {
            "key": row.requirement_key,
            "description": row.description,
            "kind": row.kind,
            "source_message_id": str(row.source_message_id),
            "source_quote": source.content[row.quote_start : quote_end],
            "minimum_count": row.minimum_count,
            "artifact_kind": row.artifact_kind,
            "asset_kind": row.asset_kind,
        }
    return definitions


def _latest_user_input(session, task):
    row = session.scalar(
        select(AgentTaskInput)
        .where(
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision <= task.applied_input_revision,
            AgentTaskInput.applied_plan_revision.is_not(None),
        )
        .order_by(AgentTaskInput.input_revision.desc())
        .limit(1)
    )
    if row is None:
        return None
    from short_drama.domain.agent import AgentMessage

    return session.get(AgentMessage, row.message_id)


def _revise_locked(session, task, current, item, source):
    """A smaller target requires a newer, explicit user instruction with that count."""
    from short_drama.agent.task.intake import explicit_count

    quote = item["source_quote"]
    marker = re.search(r"(?:改为|改成|调整为|减少到|减为|只要|缩减为)\s*", quote)
    explicit = marker and explicit_count(quote[marker.end() :]) == item["minimum_count"]
    if (
        source.input_revision <= current.input_revision
        or not explicit
        or (item["kind"], item["artifact_kind"], item["asset_kind"])
        != (current.kind, current.artifact_kind, current.asset_kind)
    ):
        raise WorkflowError(
            "invalid_tool_arguments", "修订须引用更新的用户原话，明确新数量并保持交付对象", 422
        )
    current.state, current.updated_at = "superseded", utcnow()
    key = f"m{source.id}-revision-{digest(current.requirement_key)[:16]}"
    row = _insert(
        session,
        task,
        source.id,
        requirement_key=key,
        description=item["description"],
        kind=item["kind"],
        origin="user",
        state="confirmed",
        minimum_count=item["minimum_count"],
        artifact_kind=item["artifact_kind"],
        asset_kind=item["asset_kind"],
        quote_start=source.content.index(quote),
        quote_end=source.content.index(quote) + len(quote),
    )
    row.input_revision = source.input_revision
    # A server draft of the same revised object is the same request, not extra work.
    for draft in draft_rows(session, task):
        if draft.source_message_id == source.id and (
            draft.kind,
            draft.artifact_kind,
            draft.asset_kind,
        ) == (item["kind"], item["artifact_kind"], item["asset_kind"]):
            draft.state, draft.updated_at = "superseded", utcnow()
    return row


def _reject_superseded(rows, key):
    if key in rows and rows[key].state == "superseded":
        raise WorkflowError(
            "invalid_tool_arguments",
            "此要求已被新的用户指令替代，请读取当前交付要求后使用有效 key",
            422,
            details={"superseded_key": key},
        )


def confirm_locked(session, conversation, task, operation, values, settings):
    """Confirm drafts; explicit newer user instructions can supersede their targets."""
    from short_drama.dao.agent_transcript_dao import AgentTranscriptDAO

    messages = {
        str(row.id): row
        for row in AgentTranscriptDAO(session).input_messages(
            task.id,
            task.applied_input_revision,
            {int(item.source_message_id) for item in values.additions},
        )
    }
    if not load_task_requirements(session, task, states=("draft", "confirmed", "frozen")):
        message = _latest_user_input(session, task)
        if message is not None:
            seed_requirements_locked(session, task, message)
    drafts = {row.requirement_key: row for row in draft_rows(session, task)}
    confirmed = {row.requirement_key: row for row in load_task_requirements(session, task)}
    superseded = {
        row.requirement_key: row
        for row in load_task_requirements(session, task, states=("superseded",))
    }
    unknown = [key for key in values.keys if key not in drafts and key not in confirmed]
    if unknown:
        raise WorkflowError(
            "invalid_tool_arguments",
            "keys 只能引用服务端给出的待确认要求",
            422,
            details={"unknown_keys": unknown[:8], "drafts": sorted(drafts)},
        )
    for key in values.keys:
        row = drafts.get(key)
        if row is None:
            continue
        row.state = "confirmed"
        row.updated_at = utcnow()
        confirmed[key] = row
    added = []
    for item in values.additions:
        _reject_superseded(superseded, item.key)
        if item.key in confirmed or item.key in drafts:
            current = confirmed.get(item.key) or drafts[item.key]
            if not source_valid(item, messages, set()):
                raise WorkflowError("invalid_tool_arguments", "补充要求须引用真实用户原文", 422)
            if item.minimum_count < current.minimum_count:
                added.append(
                    _revise_locked(
                        session,
                        task,
                        current,
                        item.model_dump(),
                        messages[str(item.source_message_id)],
                    )
                )
                confirmed.pop(item.key, None)
                superseded[item.key] = current
                continue
            current.minimum_count = item.minimum_count
            current.definition_hash = digest(
                {"previous": current.definition_hash, "minimum_count": item.minimum_count}
            )
            current.state = "confirmed"
            current.updated_at = utcnow()
            continue
        if not source_valid(item, messages, set()):
            raise WorkflowError("invalid_tool_arguments", "补充要求须引用真实用户原文", 422)
        content = messages[str(item.source_message_id)].content
        start = content.find(item.source_quote)
        row = _insert(
            session,
            task,
            int(item.source_message_id),
            requirement_key=item.key,
            description=item.description,
            kind=item.kind,
            origin="agent",
            state="confirmed",
            minimum_count=item.minimum_count,
            artifact_kind=item.artifact_kind,
            asset_kind=item.asset_kind,
            quote_start=start,
            quote_end=start + len(item.source_quote),
        )
        added.append(row)
    session.flush()
    rows = load_task_requirements(session, task)
    project_contract(task, rows, intent=values.intent)
    operation.result_refs = [
        {"kind": "requirement", "id": str(row.id), "key": row.requirement_key} for row in rows
    ]
    return {
        "confirmed": sorted(confirmed),
        "added": [row.requirement_key for row in added],
        "missing_drafts": sorted(drafts),
        "intent": values.intent,
    }


def record_legacy_requirements(session, task, requirements, messages):
    """Mirror a legacy ``set_task_requirements`` call into the authoritative table."""
    existing = {
        row.requirement_key: row
        for row in load_task_requirements(
            session, task, states=("draft", "confirmed", "frozen", "superseded")
        )
    }
    for definition in requirements:
        key = definition["key"]
        _reject_superseded(existing, key)
        source = messages.get(str(definition["source_message_id"]))
        if source is None:
            continue
        quote = definition["source_quote"]
        start = source.content.find(quote)
        if start < 0:
            continue
        row = existing.get(key)
        if row is not None:
            if definition["minimum_count"] < row.minimum_count:
                _revise_locked(session, task, row, definition, source)
                continue
            row.minimum_count = definition["minimum_count"]
            row.state = "confirmed"
            row.updated_at = utcnow()
            continue
        _insert(
            session,
            task,
            int(definition["source_message_id"]),
            requirement_key=key,
            description=definition["description"],
            kind=definition["kind"],
            origin="agent",
            state="confirmed",
            minimum_count=definition["minimum_count"],
            artifact_kind=definition.get("artifact_kind"),
            asset_kind=definition.get("asset_kind"),
            quote_start=start,
            quote_end=start + len(quote),
        )
    session.flush()
    project_contract(task, load_task_requirements(session, task))


def _run_writes(session, task, run):
    return session.scalars(
        select(AgentOperation).where(
            AgentOperation.task_id == task.id,
            AgentOperation.run_id == run.id,
            AgentOperation.status == "succeeded",
            AgentOperation.tool_name.in_(WRITE_TOOLS),
        )
    ).all()


ARTIFACT_KINDS = frozenset({"asset", "shot", "canvas_node", "sound_line", "media"})
DERIVED_KEY = "derived-writes"


def _written_kinds(operations):
    """Only the artifact kinds the requirement contract can express."""
    kinds = set()
    for operation in operations:
        for ref in operation.result_refs or []:
            if ref.get("kind") in ARTIFACT_KINDS and ref.get("status") != "deleted":
                kinds.add(ref["kind"])
        for change in operation.changes or []:
            kind = (change.get("after") or {}).get("kind")
            if kind in ARTIFACT_KINDS:
                kinds.add(kind)
    return kinds


def guard_publish_locked(session, task, run):
    """Refuse to publish work that was saved without a confirmed, verifiable ask."""
    if session is None or policy(task, "contract") != "server_draft.v1":
        return None
    writes = _run_writes(session, task, run)
    if not writes:
        return None
    rows = load_task_requirements(session, task)
    write_rows = [row for row in rows if row.kind == "write"]
    if not write_rows:
        return {
            "approved": False,
            "missing": [
                {
                    "reason": "本次运行保存了成果，但没有已确认的写类交付要求；"
                    "请先确认服务端给出的待确认要求，或说明这些成果对应的用户原话"
                }
            ],
        }
    if any(row.artifact_kind is None for row in write_rows):
        return None
    written = _written_kinds(writes)
    if written and not written <= {row.artifact_kind for row in write_rows}:
        if not any(row.requirement_key == DERIVED_KEY for row in rows):
            message = _latest_user_input(session, task)
            # A generic write requirement covers any saved artifact kind, so a
            # gap never invents an artifact kind the contract cannot express.
            _insert(
                session,
                task,
                message.id if message is not None else run.trigger_message_id,
                requirement_key=DERIVED_KEY,
                description="本次运行保存的作品成果",
                kind="write",
                origin="server_derived",
                state="confirmed",
                minimum_count=1,
                artifact_kind=None,
                asset_kind=None,
            )
            session.flush()
            project_contract(task, load_task_requirements(session, task))
    return None
