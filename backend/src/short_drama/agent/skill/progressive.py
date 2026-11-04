"""Method indexes and canonical load receipts for the Harness context window."""

import hashlib
from copy import deepcopy

from sqlalchemy import or_, select, tuple_

from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.resources.contracts import HydrationIntent, ResourceRef
from short_drama.agent.resources.resolver import ResourceResolver
from short_drama.agent.skill.contracts import activation_summary, activations
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.time import utcnow
from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput
from short_drama.domain.skills.catalog.skill import AgentSkillVersion
from short_drama.service.skills.catalog.skill import AgentSkillService
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
            "exclusions",
            "content_version",
            "version_id",
            "package_checksum",
            "modes",
            "required_tools",
            "builtin",
        )
        if key in skill
    }


def freeze_catalog_locked(session, owner, settings, *, tool_lookup, selected=()):
    """Pin metadata/identity only. Immutable instructions and resources remain deferred."""
    service = AgentSkillService(session, settings, tool_lookup=tool_lookup)
    result, offset = [], 0
    while True:
        page = service._list_locked(owner, offset, 100, enabled=True)
        versions = (
            dict(
                session.execute(
                    select(AgentSkillVersion.skill_key, AgentSkillVersion.id).where(
                        tuple_(AgentSkillVersion.skill_key, AgentSkillVersion.content_version).in_(
                            [(str(item.id), int(item.content_version)) for item in page.items]
                        ),
                        or_(
                            AgentSkillVersion.owner_user_id == owner,
                            AgentSkillVersion.owner_user_id.is_(None),
                        ),
                    )
                ).all()
            )
            if page.items
            else {}
        )
        for summary in page.items:
            item = skill_index(summary.model_dump(mode="json"))
            item["version_id"] = str(versions[item["id"]])
            item["issues"] = summary.issues
            result.append(item)
        offset += len(page.items)
        if offset >= page.total:
            by_id = {item["id"]: item for item in result}
            by_id.update((item["id"], skill_index(item)) for item in selected)
            return list(by_id.values())


def catalog_locked(
    session,
    owner,
    task,
    settings=None,
    *,
    state,
    run,
    query="",
    offset=0,
    limit=12,
    include_manual=True,
    search=False,
):
    frozen = run.config_snapshot["skill_catalog"]
    excluded = set(state.get("excluded_skill_ids", []))
    candidates = [
        item
        for item in frozen
        if item["id"] not in excluded
        and (
            not query
            or query.casefold()
            in " ".join(
                str(item.get(key, "")) for key in ("name", "description", "exclusions")
            ).casefold()
        )
    ]
    total = len(candidates)
    candidates = candidates[offset : offset + limit]
    items, used = [], 0
    consumed = 0
    for candidate in candidates:
        item = skill_index(candidate)
        maximum = 800 if search else 180
        description, exclusions = item.get("description", ""), item.get("exclusions", "")
        item["description"] = description[:maximum]
        item["exclusions"] = exclusions[:maximum]
        item["truncated"] = len(description) > maximum or len(exclusions) > maximum
        if search:
            text = description + "\n" + exclusions
            hit = max(0, text.casefold().find(query.casefold()) - 120) if query else 0
            item["match_excerpt"] = text[hit : hit + 480]
        item["issues"] = candidate.get("issues", [])
        item["uri"] = f"skill://{item.get('slug') or item['id']}"
        cost = (len(str(item).encode()) + 2) // 3
        if items and used + cost > (6000 if search else INDEX_TOKENS):
            break
        consumed += 1
        used += cost
        items.append(item)
    return {
        "items": items,
        "next_offset": offset + consumed if offset + consumed < total else None,
        "total": total,
        "estimated_tokens": used,
    }


def _matches(item, name, version):
    return name.removeprefix("skill://") in {
        item.get("id"),
        item.get("name"),
        item.get("slug"),
    } and str(item["content_version"]) == str(version)


def load_locked(
    session, conversation, task, operation, values, settings=None, *, state, tool_lookup
):
    run = session.get(AgentRun, operation.run_id)
    known = list(run.config_snapshot["skill_catalog"])
    records = deepcopy(activations(state))
    known.extend(row["skill"] for row in records)
    excluded = set(state.get("excluded_skill_ids", []))
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
            if item["active"]
            and item.get("scope_id") == str(operation.scope_id)
            and _matches(item["skill"], values.name, values.content_version)
        ),
        None,
    )
    if existing:
        if any(item["id"] == existing["skill"]["id"] for item in state.get("selected_skills", [])):
            existing["source"] = "manual"
            state["skill_activations"] = records
        skill = existing["skill"]
        canonical_id = existing["body_operation_id"]
        in_context = canonical_id in state.get("skill_body_operations", [])
        return {
            **activation_summary(existing),
            "skill_ref": existing["reference"],
            "already_loaded": in_context,
            "body_operation_id": canonical_id,
            "note": "该版本正文已在当前上下文中" if in_context else "复用原加载回执恢复正文",
        }
    indexed = next(iter(matches.values()), None)
    if indexed is None:
        if any(
            item.get("active")
            and values.name.removeprefix("skill://")
            in {item["skill"].get(key) for key in ("id", "name", "slug")}
            for item in records
        ):
            raise WorkflowError(
                "assistant_skill_version_frozen", "方法版本已冻结；更换版本须由用户明确选择", 409
            )
        raise WorkflowError(
            "assistant_skill_not_discovered", "请从索引或检索目录选择真实方法版本", 409
        )
    manual = next(
        (item for item in state.get("selected_skills", []) if item["id"] == indexed["id"]),
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
    version = session.get(AgentSkillVersion, int(indexed["version_id"]))
    if (
        version is None
        or version.owner_user_id not in {None, conversation.owner_user_id}
        or version.skill_key != indexed["id"]
        or version.checksum_sha256 != indexed.get("package_checksum")
    ):
        raise NotFound("冻结的方法版本不可访问")
    skill = {
        **indexed,
        **version.package_metadata,
        "instructions": version.instructions,
        "resources": version.resources,
    }
    from short_drama.service.skills.packages.skill_package import platform_tool_issues

    issues = platform_tool_issues(skill.get("required_tools", []), tool_lookup)
    if issues:
        raise Conflict("；".join(issues))
    mode = (task.execution_target or {}).get("mode")
    if mode and mode not in skill.get("modes", []):
        raise Conflict("方法不适用于当前任务模式")
    missing = set(skill.get("required_tools", [])) - set(state.get("permitted_tool_names", []))
    if missing:
        raise Conflict("当前范围缺少方法所需工具：" + "、".join(sorted(missing)))
    cost = (len(skill["instructions"].encode()) + 2) // 3
    active = [item for item in records if item["active"]]
    committed_active = active[:]
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
        raise WorkflowError(
            "assistant_skill_budget",
            "活动方法已达配额，请先释放可释放的自动方法再加载",
            409,
            details={
                "active_count": len(committed_active),
                "max_active": 8,
                "body_tokens": sum(item.get("body_tokens", 0) for item in committed_active),
                "max_body_tokens": ACTIVE_SKILL_TOKENS,
                "requested_body_tokens": cost,
                "releasable": [
                    {
                        "activation_id": item["id"],
                        "source": item["source"],
                        "body_tokens": item["body_tokens"],
                    }
                    for item in committed_active
                    if item["source"] == "auto"
                ],
            },
        )
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
                AgentOperation.scope_id == operation.scope_id,
                AgentOperation.tool_name == "runtime__load_skill",
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
        "scope_id": str(operation.scope_id),
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
    state["skill_activations"] = records
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
        "exclusions": skill.get("exclusions", ""),
        "already_loaded": False,
    }


def release_locked(session, conversation, task, operation, values, settings=None, *, state):
    records = deepcopy(activations(state))
    item = next(
        (
            row
            for row in records
            if row["id"] == str(values.activation_id)
            and row.get("scope_id") == str(operation.scope_id)
        ),
        None,
    )
    if item is None:
        raise NotFound("当前任务中没有此方法激活记录")
    if item["source"] == "manual":
        raise Conflict("手选方法由用户管理，不能由 Agent 自动释放")
    already = not item["active"]
    item["active"] = False
    item["issue"] = "当前阶段已完成，正文不再独立保留"
    state["skill_activations"] = records
    return {**activation_summary(item), "already_applied": already}
