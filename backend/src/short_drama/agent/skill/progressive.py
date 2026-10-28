"""Method indexes and canonical load receipts for the Harness context window."""

import hashlib
from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.resources.contracts import HydrationIntent, ResourceRef
from short_drama.agent.resources.resolver import ResourceResolver
from short_drama.agent.skill.contracts import activation_summary, activations
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.time import utcnow
from short_drama.dao.agent_transcript_dao import AgentTranscriptDAO
from short_drama.domain.agent import AgentRun
from short_drama.domain.agent_skill import AgentSkillVersion
from short_drama.domain.agent_task import AgentOperation, AgentTaskInput
from short_drama.schemas.agent_context import SkillSelection
from short_drama.service.agent_skill_service import AgentSkillService, freeze_skills
from short_drama.utils.snowflake import next_id

INDEX_TOKENS = 2000
ACTIVE_SKILL_TOKENS = 8000


def skill_index(skill: dict) -> dict:
    return {
        key: deepcopy(skill[key])
        for key in (
            "id",
            "name",
            "slug",
            "description",
            "content_version",
            "version_id",
            "package_checksum",
            "modes",
            "required_tools",
            "builtin",
        )
        if key in skill
    }


def catalog_locked(
    session, owner, task, settings=None, *, query="", offset=0, limit=12, include_manual=True
):
    page = AgentSkillService(session, settings)._list_locked(
        owner,
        offset,
        limit,
        query=query,
        enabled=True,
        mode=(task.execution_target or {}).get("mode"),
        compatible_only=True,
        exclude_keys=task.effective_state.get("excluded_skill_ids", []),
    )
    items, used = [], 0
    manual = {item["id"]: item for item in task.selected_skills} if include_manual else {}
    candidates = [(item, False) for item in manual.values()] + [
        (item.model_dump(mode="json"), True) for item in page.items
    ]
    seen = set()
    consumed = 0
    for candidate, paged in candidates:
        if candidate["id"] in seen:
            consumed += int(paged)
            continue
        item = skill_index(candidate)
        # The stable index is deliberately small. Full descriptions remain in
        # the paged directory; no instruction text is copied into the index.
        item["description"] = item.get("description", "")[:180]
        item["uri"] = f"skill://{item.get('slug') or item['id']}"
        cost = (len(str(item).encode()) + 2) // 3
        if used + cost > INDEX_TOKENS:
            break
        consumed += int(paged)
        used += cost
        items.append(item)
        seen.add(item["id"])
    return {
        "items": items,
        "next_offset": offset + consumed if offset + consumed < page.total else None,
        "total": page.total,
        "estimated_tokens": used,
    }


def _matches(item, name, version):
    return name.removeprefix("skill://") in {
        item.get("id"),
        item.get("name"),
        item.get("slug"),
    } and str(item["content_version"]) == str(version)


def load_locked(session, conversation, task, operation, values, settings=None):
    run = session.get(AgentRun, operation.run_id)
    known = (run.checkpoint or {}).get("skill_index", []) + task.selected_skills
    for result in session.scalars(
        select(AgentOperation.result)
        .where(
            AgentOperation.task_id == task.id,
            AgentOperation.tool_name == "search_skills",
            AgentOperation.status == "succeeded",
        )
        .order_by(AgentOperation.id.desc())
        .limit(32)
    ):
        known.extend((result or {}).get("items", []))
    records = deepcopy(activations(task))
    known.extend(row["skill"] for row in records)
    excluded = set(task.effective_state.get("excluded_skill_ids", []))
    if any(
        item["id"] in excluded and _matches(item, values.name, values.content_version)
        for item in [*known, *(entry["skill"] for entry in records)]
    ):
        raise WorkflowError("assistant_skill_excluded", "用户已移除此方法，不能自动重新加载", 409)
    matches = {
        item["id"]: item for item in known if _matches(item, values.name, values.content_version)
    }
    if len(matches) > 1:
        raise WorkflowError(
            "assistant_skill_ambiguous", "方法名称存在歧义，请使用目录中的唯一 id", 409
        )
    existing = next(
        (
            item
            for item in records
            if item["active"] and _matches(item["skill"], values.name, values.content_version)
        ),
        None,
    )
    if existing:
        if any(item["id"] == existing["skill"]["id"] for item in task.selected_skills):
            existing["source"] = "manual"
            task.effective_state = {**task.effective_state, "skill_activations": records}
        skill = existing["skill"]
        canonical_id = existing["body_operation_id"]
        in_context = canonical_id in (run.checkpoint or {}).get("skill_body_operations", [])
        return {
            **activation_summary(existing),
            "skill_ref": existing["reference"],
            "already_loaded": in_context,
            "body_operation_id": canonical_id,
            "note": "该版本正文已在当前上下文中" if in_context else "复用原加载回执恢复正文",
        }
    indexed = next(iter(matches.values()), None)
    if indexed is None:
        raise WorkflowError(
            "assistant_skill_not_discovered", "请从索引或检索目录选择真实方法版本", 409
        )
    manual = next(
        (item for item in task.selected_skills if item["id"] == indexed["id"]),
        None,
    )
    selected_source = None
    if manual:
        selected_source = session.scalar(
            select(AgentTaskInput.message_id)
            .where(
                AgentTaskInput.task_id == task.id,
                AgentTaskInput.input_revision <= task.applied_input_revision,
                AgentTaskInput.applied_plan_revision.is_not(None),
                AgentTaskInput.payload["selected_skills"].is_not(None),
            )
            .order_by(AgentTaskInput.input_revision.desc())
            .limit(1)
        )
        restricted, _ = restricted_messages(session, conversation.id)
        if selected_source in restricted:
            raise Conflict("手选方法的选择来源已遗忘，请由用户重新选择后再加载")
    frozen = [
        item["skill"] for item in records if item["active"] and item["skill"]["id"] == indexed["id"]
    ]
    if any(
        str(item["content_version"]) != str(values.content_version)
        for item in [*frozen, *([manual] if manual else [])]
    ):
        raise WorkflowError(
            "assistant_skill_version_frozen", "方法版本已冻结；更换版本须由用户明确选择", 409
        )
    if manual and manual.get("version_id"):
        version = session.get(AgentSkillVersion, int(manual["version_id"]))
        if (
            version is None
            or version.owner_user_id not in {None, conversation.owner_user_id}
            or version.checksum_sha256 != manual.get("package_checksum")
        ):
            raise NotFound("手动指定的方法版本不可访问")
        skill = {**manual, "instructions": version.instructions, "resources": version.resources}
    else:
        skill = freeze_skills(
            session,
            conversation.owner_user_id,
            [
                SkillSelection(id=indexed["id"], content_version=values.content_version),
            ],
        )[0]
    mode = (task.execution_target or {}).get("mode")
    if mode and mode not in skill.get("modes", []):
        raise Conflict("方法不适用于当前任务模式")
    if set(skill.get("required_tools", [])) - set(
        (run.checkpoint or {}).get(
            "permitted_tool_names", (run.checkpoint or {}).get("available_tool_names", [])
        )
    ):
        raise Conflict("当前任务缺少方法所需工具")
    cost = (len(skill["instructions"].encode()) + 2) // 3
    active = [item for item in records if item["active"]]
    if manual:
        for item in reversed(active[:]):
            if (
                len(active) < 8
                and sum(row.get("body_tokens", 0) for row in active) + cost <= ACTIVE_SKILL_TOKENS
            ):
                break
            if item["source"] == "auto":
                item["active"] = False
                active.remove(item)
    if (
        len(active) >= 8
        or sum(item.get("body_tokens", 0) for item in active) + cost > ACTIVE_SKILL_TOKENS
    ):
        raise WorkflowError("assistant_skill_budget", "活动方法正文超过独立保留配额", 409)
    reference = ResourceRef(
        kind="skill",
        ref_id=f"skill:{skill['id']}:{skill['content_version']}",
        uri=f"skill://{skill.get('slug') or skill['id']}",
        source="skill_version",
        version=str(skill["content_version"]),
        checksum=skill.get("package_checksum"),
        metadata={
            "version_id": skill.get("version_id"),
            "body_checksum": hashlib.sha256(skill["instructions"].encode()).hexdigest(),
        },
    )
    previous = next(
        (
            row
            for row in reversed(records)
            if not row["active"] and _matches(row["skill"], values.name, values.content_version)
        ),
        None,
    )
    if previous is None:
        previous = session.scalar(
            select(AgentOperation.result["activation_record"])
            .where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name == "load_skill",
                AgentOperation.status == "succeeded",
                AgentOperation.result["skill_id"].as_string() == str(skill["id"]),
                AgentOperation.result["content_version"].as_string()
                == str(skill["content_version"]),
            )
            .order_by(AgentOperation.id.desc())
            .limit(1)
        )
    canonical_id = str(operation.id)
    if previous and previous.get("reference") == reference.model_dump(mode="json"):
        restricted, _ = restricted_messages(session, conversation.id)
        prior = AgentTranscriptDAO(session).evidence(task.id, [int(previous["body_operation_id"])])
        if (
            prior
            and prior[0].turn_id > run.checkpoint.get("history_floor_turn_id", 0)
            and int(previous["source_message_id"]) not in restricted
        ):
            canonical_id = previous["body_operation_id"]
    hydrated = ResourceResolver(None).hydrate(
        HydrationIntent(
            resource=reference,
            purpose=values.purpose,
            tool_call_id=str(operation.tool_call_id),
            invocation_id=f"operation:{operation.id}",
        ),
        protocol="text",
        skill_body=skill["instructions"],
    )
    item = {
        "id": str(next_id()),
        "task_id": str(task.id),
        "skill": skill_index(skill),
        "source": "manual" if manual else "auto",
        "source_message_id": str(
            selected_source
            or session.scalar(
                select(AgentTaskInput.message_id).where(
                    AgentTaskInput.task_id == task.id,
                    AgentTaskInput.input_revision == operation.input_revision,
                )
            )
        ),
        "operation_id": str(operation.id),
        "body_operation_id": canonical_id,
        "purpose": values.purpose,
        "active": True,
        "body_tokens": cost,
        "plan_revision": str(task.plan_revision),
        "created_at": utcnow().isoformat() + "Z",
        "resource_reads": [],
        "reference": reference.model_dump(mode="json"),
    }
    records = [
        *([row for row in records if not row["active"]][-16:]),
        *[row for row in records if row["active"]],
        item,
    ]
    task.effective_state = {**task.effective_state, "skill_activations": records}
    operation.result_refs = [{"kind": "skill", **activation_summary(item)}]
    return {
        **activation_summary(item),
        "skill_ref": reference.model_dump(mode="json"),
        "body_operation_id": canonical_id,
        **(
            {"instructions": hydrated.content}
            if canonical_id == str(operation.id)
            else {"note": "复用该版本原正文回执重新激活"}
        ),
        "activation_record": item,
        "resources": skill.get("resources", []),
        "already_loaded": False,
    }


def release_locked(session, conversation, task, operation, values, settings=None):
    records = deepcopy(activations(task))
    item = next((row for row in records if row["id"] == str(values.activation_id)), None)
    if item is None:
        raise NotFound("当前任务中没有此方法激活记录")
    if item["source"] == "manual":
        raise Conflict("手选方法由用户管理，不能由 Agent 自动释放")
    already = not item["active"]
    item["active"] = False
    item["issue"] = "当前阶段已完成，正文不再独立保留"
    task.effective_state = {**task.effective_state, "skill_activations": records}
    return {**activation_summary(item), "already_applied": already}
