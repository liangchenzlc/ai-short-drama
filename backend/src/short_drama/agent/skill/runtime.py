"""Task-scoped immutable methods; directory settings only govern new activations."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.skill.contracts import MAX_ACTIVATIONS as MAX_ACTIVATIONS
from short_drama.agent.skill.contracts import MAX_ACTIVE as MAX_ACTIVE
from short_drama.agent.skill.contracts import MAX_INSTRUCTION_BYTES as MAX_INSTRUCTION_BYTES
from short_drama.agent.skill.contracts import (
    MAX_RESOURCE_CONTEXT_BYTES as MAX_RESOURCE_CONTEXT_BYTES,
)
from short_drama.agent.skill.contracts import SKILL_LOAD as SKILL_LOAD
from short_drama.agent.skill.contracts import SKILL_RESOURCE as SKILL_RESOURCE
from short_drama.agent.skill.contracts import SKILL_SEARCH as SKILL_SEARCH
from short_drama.agent.skill.contracts import SKILL_TOOLS as SKILL_TOOLS
from short_drama.agent.skill.contracts import LoadSkill as LoadSkill
from short_drama.agent.skill.contracts import ReadSkillResource as ReadSkillResource
from short_drama.agent.skill.contracts import SearchSkills as SearchSkills
from short_drama.agent.skill.contracts import activation_summary as activation_summary
from short_drama.agent.skill.contracts import activations as activations
from short_drama.agent.tools.registry import TOOL_VERSION
from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput
from short_drama.domain.skills.catalog.skill import AgentSkillVersion
from short_drama.schemas.assistant.context.context import SkillSelection
from short_drama.service.base import utcnow
from short_drama.service.skills.catalog.skill import AgentSkillService
from short_drama.service.skills.catalog.versions import freeze_skills
from short_drama.utils.snowflake import next_id


def _store(task, records):
    state = deepcopy(task.effective_state)
    state["skill_activations"] = records
    task.effective_state = state


def _activate(task, skill, *, source, message_id, purpose, operation_id=None):
    records = deepcopy(activations(task))
    existing = next(
        (
            item
            for item in records
            if item["skill"]["id"] == skill["id"]
            and item["skill"]["content_version"] == skill["content_version"]
        ),
        None,
    )
    current = [item for item in records if item["active"] and item is not existing]
    if source == "manual":
        for item in reversed(current):
            if (
                len(current) < MAX_ACTIVE
                and sum(len(entry["skill"]["instructions"].encode("utf-8")) for entry in current)
                + len(skill["instructions"].encode("utf-8"))
                <= MAX_INSTRUCTION_BYTES
            ):
                break
            if item["source"] == "auto":
                item["active"] = False
                current.remove(item)
    if (
        len(current) >= MAX_ACTIVE
        or sum(len(item["skill"]["instructions"].encode("utf-8")) for item in current)
        + len(skill["instructions"].encode("utf-8"))
        > MAX_INSTRUCTION_BYTES
    ):
        raise WorkflowError(
            "assistant_skill_budget", "活动方法数量或正文超过任务上限，请减少方法", 409
        )
    if existing is not None:
        existing["active"] = True
        if source == "manual":
            existing["source"] = source
            existing["source_message_id"] = str(message_id)
            existing["purpose"] = purpose
        _store(task, records)
        return existing
    if len(records) >= MAX_ACTIVATIONS:
        raise WorkflowError("assistant_skill_budget", "本任务方法版本记录已达上限，请新建任务", 409)
    item = {
        "id": str(next_id()),
        "task_id": str(task.id),
        "skill": deepcopy(skill),
        "source": source,
        "source_message_id": str(message_id),
        "operation_id": str(operation_id) if operation_id is not None else None,
        "purpose": purpose,
        "active": True,
        "plan_revision": str(task.plan_revision),
        "created_at": utcnow().isoformat() + "Z",
        "resource_reads": [],
    }
    records.append(item)
    _store(task, records)
    return item


def apply_manual_skills(task, selected, message_id):
    """Applied only at the input boundary; ordinary supplements preserve the methods."""
    records = deepcopy(activations(task))
    selected_keys = {(item["id"], item["content_version"]) for item in selected}
    selected_ids = {item["id"] for item in selected}
    removed = {
        item["skill"]["id"]
        for item in records
        if item["active"] and item["source"] == "manual" and item["skill"]["id"] not in selected_ids
    }
    for item in records:
        if item["source"] == "manual" or item["skill"]["id"] in selected_ids:
            item["active"] = (
                item["skill"]["id"],
                item["skill"]["content_version"],
            ) in selected_keys
    _store(task, records)
    state = deepcopy(task.effective_state)
    state["excluded_skill_ids"] = sorted(
        (set(state.get("excluded_skill_ids", [])) | removed) - selected_ids
    )
    task.effective_state = state
    for skill in selected:
        _activate(task, skill, source="manual", message_id=message_id, purpose="用户手动指定")


def _mode_issue(task, skill):
    mode = (task.execution_target or {}).get("mode")
    return "该方法不适用于当前写入目标模式" if mode and mode not in skill.get("modes", []) else None


def skill_context(task, available_tools=None):
    """Reassemble exact methods and read fragments after history compression or restart."""
    result = []
    for item in sorted(activations(task), key=lambda item: item["source"] != "manual"):
        if not item["active"]:
            continue
        skill = item["skill"]
        issue = _mode_issue(task, skill)
        missing = set(skill.get("required_tools", [])) - set(available_tools or ())
        if missing and available_tools is not None:
            issue = "当前任务尚无方法所需工具：" + "、".join(sorted(missing))
        result.append(
            {
                **activation_summary(item),
                "issue": issue,
                "instructions": None if issue else skill["instructions"],
                "resources": skill.get("resources", []),
                "read_resources": [] if issue else item["resource_reads"],
                "stale_hint": item.get("stale_hint"),
            }
        )
    return result


def refresh_skill_context(task, available_tools):
    context = skill_context(task, available_tools)
    issues = {item["activation_id"]: item["issue"] for item in context}
    records = deepcopy(activations(task))
    changed = False
    for item in records:
        if item["active"] and item.get("issue") != issues[item["id"]]:
            item["issue"] = issues[item["id"]]
            changed = True
    if changed:
        _store(task, records)
        task.row_version += 1
    return context


def _bound(conversation, task, operation):
    if (
        conversation.scope_version != 3
        or task.conversation_id != conversation.id
        or operation.task_id != task.id
    ):
        raise NotFound("任务方法不可访问")


def search_skills_locked(session, conversation, task, values, settings):
    page = AgentSkillService(session, settings)._list_locked(
        conversation.owner_user_id,
        values.offset,
        values.limit,
        query=values.query or None,
        enabled=True,
        mode=(task.execution_target or {}).get("mode"),
        compatible_only=True,
        exclude_keys=task.effective_state.get("excluded_skill_ids", []),
    )
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "total": page.total,
        "offset": page.offset,
        "next_offset": page.offset + page.limit if page.offset + page.limit < page.total else None,
    }


def load_skill_locked(session, conversation, task, operation, values, available_tools):
    _bound(conversation, task, operation)
    key = values.skill_id
    if key in task.effective_state.get("excluded_skill_ids", []):
        raise WorkflowError("assistant_skill_excluded", "用户已移除此方法，不能自动重新加载", 409)
    existing = next(
        (item for item in activations(task) if item["active"] and item["skill"]["id"] == key),
        None,
    )
    if existing and int(existing["skill"]["content_version"]) != values.content_version:
        raise WorkflowError(
            "assistant_skill_version_frozen",
            "本任务已加载此方法的冻结版本；如需更换，请由用户明确选择新版本",
            409,
        )
    if existing is None:
        # Manually-selected skills can be loaded directly without prior search.
        manual_selected_ids = {
            item["id"] for item in (task.context_snapshot or {}).get("selected_skills", [])
        }
        if key not in manual_selected_ids:
            discovered = session.scalars(
                select(AgentOperation)
                .where(
                    AgentOperation.task_id == task.id,
                    AgentOperation.tool_name == SKILL_SEARCH,
                    AgentOperation.status == "succeeded",
                )
                .order_by(AgentOperation.id.desc())
            ).all()
            if not any(
                any(
                    item["id"] == key and int(item["content_version"]) == values.content_version
                    for item in (entry.result or {}).get("items", [])
                )
                for entry in discovered
            ):
                raise WorkflowError(
                    "assistant_skill_not_discovered", "请先检索实际可用的 Skill 版本", 409
                )
        skill = freeze_skills(
            session,
            conversation.owner_user_id,
            [SkillSelection(id=key, content_version=values.content_version)],
        )[0]
    else:
        skill = existing["skill"]
    issue = _mode_issue(task, skill)
    missing = set(skill.get("required_tools", [])) - set(available_tools)
    if issue or missing:
        raise WorkflowError(
            "assistant_skill_unavailable",
            issue or "此任务未获准使用方法所需工具：" + "、".join(sorted(missing)),
            409,
        )
    message_id = session.scalar(
        select(AgentTaskInput.message_id).where(
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision == task.applied_input_revision,
        )
    )
    item = _activate(
        task,
        skill,
        source="auto",
        message_id=message_id,
        purpose=values.purpose,
        operation_id=operation.id,
    )
    operation.result_refs = [{"kind": "skill", **activation_summary(item)}]
    task.row_version += 1
    return {
        **activation_summary(item),
        "instructions": skill["instructions"],
        "resources": skill.get("resources", []),
        "already_loaded": existing is not None,
    }


def resource_manifest_locked(session, conversation, task, operation, values):
    _bound(conversation, task, operation)
    activation = next(
        (
            item
            for item in activations(task)
            if item["id"] == str(values.activation_id)
            and item["active"]
            and item["task_id"] == str(task.id)
        ),
        None,
    )
    if activation is None:
        raise NotFound("本任务尚未激活此方法版本")
    skill = activation["skill"]
    if _mode_issue(task, skill):
        raise WorkflowError("assistant_skill_unavailable", "方法不适用于当前目标，请核对方向", 409)
    version = (
        session.get(AgentSkillVersion, int(skill["version_id"]))
        if skill.get("version_id")
        else None
    )
    if (
        version is None
        or version.owner_user_id not in {None, conversation.owner_user_id}
        or version.skill_key != skill["id"]
        or version.content_version != int(skill["content_version"])
        or version.checksum_sha256 != skill.get("package_checksum")
    ):
        raise NotFound("已激活的 Skill 版本不可读取")
    resource = next((dict(item) for item in version.resources if item["path"] == values.path), None)
    if resource is None:
        raise NotFound("方法的冻结目录中没有此资料")
    return resource


def finish_resource_locked(session, conversation, task, operation, values, prepared):
    manifest = resource_manifest_locked(session, conversation, task, operation, values)
    if manifest != prepared["manifest"]:
        raise WorkflowError(
            "assistant_skill_resource_changed", "冻结资料校验不一致，请核对原版本", 409
        )
    result = prepared["result"]
    if operation.tool_version in {2, 3, TOOL_VERSION}:
        operation.result_refs = [
            {
                "kind": "skill_resource",
                "activation_id": str(values.activation_id),
                "path": values.path,
                "offset": values.offset,
            }
        ]
        return result
    records = deepcopy(activations(task))
    activation = next(item for item in records if item["id"] == str(values.activation_id))
    if result not in activation["resource_reads"]:
        size = sum(
            len(item["content"].encode("utf-8"))
            for entry in records
            for item in entry["resource_reads"]
        )
        if size + len(result["content"].encode("utf-8")) > MAX_RESOURCE_CONTEXT_BYTES:
            raise WorkflowError(
                "assistant_skill_resource_budget",
                "任务方法资料上下文已达 32 KiB 上限，请缩小读取范围",
                409,
            )
        activation["resource_reads"].append(result)
        _store(task, records)
    operation.result_refs = [
        {
            "kind": "skill_resource",
            **activation_summary(activation),
            "path": values.path,
            "offset": values.offset,
            "next_offset": result["next_offset"],
        }
    ]
    return result


def read_resource_bytes(settings, manifest):
    from short_drama.storage.minio import MinioStorage

    storage = MinioStorage(settings)
    try:
        return AgentSkillService(None, settings, storage)._resource_bytes(manifest)
    finally:
        storage.close()


def prepare_resource_read(factory, settings, run_id, operation_id, fence):
    """Read immutable storage outside SQL locks, then revalidate before publishing."""
    from short_drama.agent.core.access import lock_run
    from short_drama.agent.core.fence import owns_execution
    from short_drama.agent.task.queries import task_for_run

    read_fence = None
    try:
        with factory.begin() as session:
            name = session.scalar(
                select(AgentOperation.tool_name).where(
                    AgentOperation.id == operation_id,
                    AgentOperation.run_id == run_id,
                )
            )
            if name != SKILL_RESOURCE:
                return None
            rows = lock_run(session, run_id)
            if rows is None or not owns_execution(rows[2], fence):
                return None
            _project, conversation, run = rows
            read_fence = (run.message_version, run.lease_token)
            task = task_for_run(session, run)
            operation = session.get(AgentOperation, operation_id)
            if operation.status != "running" or operation.execution_epoch != run.message_version:
                return None
            values = ReadSkillResource.model_validate(operation.arguments)
            manifest = resource_manifest_locked(session, conversation, task, operation, values)
            if manifest["kind"] == "image":
                if operation.tool_version not in {2, 3, TOOL_VERSION}:
                    raise WorkflowError(
                        "agent_resource_modality", "此方法图片需要 Harness 读取", 409
                    )
                from short_drama.agent.resources.contracts import HydrationIntent
                from short_drama.agent.resources.resolver import skill_resource_ref
                from short_drama.domain.assistant.execution.models import AgentToolCall

                reference = skill_resource_ref(
                    manifest, f"skill-resource:{values.activation_id}:{values.path}"
                )
                intent = HydrationIntent(
                    resource=reference,
                    purpose=values.purpose,
                    detail=values.detail,
                    crop=values.crop,
                    tool_call_id=session.get(
                        AgentToolCall, operation.tool_call_id
                    ).provider_call_id,
                    invocation_id=f"after-operation:{operation.id}",
                )
                return {
                    "fence": read_fence,
                    "manifest": manifest,
                    "result": {
                        "activation_id": str(values.activation_id),
                        "path": values.path,
                        "image_ref": reference.model_dump(mode="json"),
                        "hydration_intent": intent.model_dump(mode="json"),
                    },
                }
        text = read_resource_bytes(settings, manifest)
        end = min(len(text), values.offset + values.limit)
        return {
            "fence": read_fence,
            "manifest": manifest,
            "result": {
                "activation_id": str(values.activation_id),
                "path": values.path,
                "kind": manifest["kind"],
                "checksum_sha256": manifest["checksum_sha256"],
                "byte_size": manifest["byte_size"],
                "offset": values.offset,
                "content": text[values.offset : end],
                "next_offset": end if end < len(text) else None,
            },
        }
    except (ValueError, BusinessError) as error:
        return {"error": error, "fence": read_fence}
