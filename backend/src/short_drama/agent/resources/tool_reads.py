from sqlalchemy import select

from short_drama.agent.context.capabilities import input_capabilities
from short_drama.agent.resources.catalog import attachments_locked
from short_drama.agent.resources.contracts import HydrationIntent
from short_drama.agent.resources.resolver import attachment_ref
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentTaskInput


def _intent(session, reference, operation, values):
    video_audio = (
        session.scalar(
            select(AgentTaskInput.payload["video_audio"].as_string()).where(
                AgentTaskInput.task_id == operation.task_id,
                AgentTaskInput.input_revision == operation.input_revision,
            )
        )
        or "include"
    )
    return HydrationIntent(
        resource=reference,
        purpose=values.purpose,
        tool_call_id=session.get(AgentToolCall, operation.tool_call_id).provider_call_id,
        invocation_id=f"after-operation:{operation.id}",
        detail=values.detail,
        crop=values.crop,
        video_audio=video_audio,
    ).model_dump(mode="json")


def read_resource_locked(session, conversation, task, operation, values, settings):
    attachment = attachments_locked(session, conversation, task).get(values.ref_id)
    if attachment is None:
        raise NotFound(
            "附件目录中没有此 ref_id。Skill 的 references、scripts 和示例图请使用 "
            "read_skill_resource(activation_id, path)，身份须来自已加载方法的资源目录。"
        )
    reference = attachment_ref(attachment)
    if reference.kind in {"image", "audio", "video"}:
        run = session.get(AgentRun, operation.run_id)
        capabilities = input_capabilities(run.config_snapshot)
        intent = _intent(session, reference, operation, values)
        if (
            reference.kind == "audio"
            and not capabilities["audio"]
            or reference.kind in {"image", "video"}
            and not capabilities["image"]
            or reference.kind == "video"
            and intent["video_audio"] != "visual_only"
            and not capabilities["audio"]
        ):
            raise WorkflowError("agent_resource_modality", "模型不支持所需媒体理解模态", 422)
        return {
            "resource_ref": reference.model_dump(mode="json"),
            "hydration_intent": intent,
        }
    if reference.kind == "text":
        text = attachment.get("text_content", "")
        # text_content is the validated ingress decoding (UTF-8 BOM/GB18030
        # included). Its checksum is for the original bytes, not this decoding.
        end = min(values.offset + values.limit, len(text))
        return {
            "resource_ref": reference.model_dump(mode="json"),
            "content": text[values.offset : end],
            "offset": values.offset,
            "next_offset": end if end < len(text) else None,
        }
    return {
        "resource_ref": reference.model_dump(mode="json"),
        "error": {
            "code": "agent_resource_modality",
            "message": "当前资源需使用支持该模态的读取策略",
        },
    }
