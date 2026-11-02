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
from short_drama.agent.skill.contracts import ReadSkillResource as ReadSkillResource
from short_drama.agent.skill.contracts import SearchSkills as SearchSkills
from short_drama.agent.skill.contracts import activation_summary as activation_summary
from short_drama.agent.skill.contracts import activations as activations
from short_drama.agent.tools.contracts import TOOL_VERSION
from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.domain.skills.catalog.skill import AgentSkillVersion
from short_drama.service.skills.catalog.skill import AgentSkillService


def _mode_issue(task, skill):
    mode = (task.execution_target or {}).get("mode")
    return "该方法不适用于当前写入目标模式" if mode and mode not in skill.get("modes", []) else None


def _bound(conversation, task, operation):
    if (
        conversation.scope_version != 3
        or task.conversation_id != conversation.id
        or operation.task_id != task.id
    ):
        raise NotFound("任务方法不可访问")


def resource_manifest_locked(session, conversation, task, operation, values, *, state):
    _bound(conversation, task, operation)
    activation = next(
        (
            item
            for item in activations(state)
            if item["id"] == str(values.activation_id)
            and item["active"]
            and item["task_id"] == str(task.id)
            and item.get("scope_id") == str(operation.scope_id)
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


def finish_resource_locked(session, conversation, task, operation, values, prepared, *, state):
    manifest = resource_manifest_locked(session, conversation, task, operation, values, state=state)
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
    records = deepcopy(activations(state))
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
        state["skill_activations"] = records
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


def prepare_resource_read(factory, settings, run_id, operation_id, fence, *, state, values=None):
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
            if values is None:
                values = ReadSkillResource.model_validate(operation.arguments)
            manifest = resource_manifest_locked(
                session, conversation, task, operation, values, state=state
            )
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
