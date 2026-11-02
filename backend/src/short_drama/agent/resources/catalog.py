"""Task-owned resource indexes; directory reads never fetch storage entities."""

from sqlalchemy import select

from short_drama.agent.context.attachments import available_attachment_ids, task_attachments
from short_drama.agent.context.delegation import delegated_inputs_locked, scope_snapshot_locked
from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.resources.resolver import attachment_ref
from short_drama.core.exceptions import NotFound
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput
from short_drama.domain.media.files.files import MediaFile
from short_drama.service.assistant.context.subject import selection_metadata


def attachments_locked(session, conversation, task):
    binding = session.info.get("assistant_scope")
    delegated = delegated_inputs_locked(session, *binding) if binding else None
    restricted, _ = restricted_messages(session, conversation.id)
    attached = task_attachments(session, task, restricted_ids=restricted)
    available = available_attachment_ids(session, conversation, attached)
    result = {key: item for key, item in attached.items() if key in available}
    snapshot = scope_snapshot_locked(session, task)
    metadata = selection_metadata(session, conversation.owner_user_id, snapshot)
    selected = {item["subject_key"] for item in metadata if item["availability"] != "unavailable"}
    for message_id, payload in session.execute(
        select(AgentTaskInput.message_id, AgentTaskInput.payload).where(
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision <= task.applied_input_revision,
        )
    ):
        if message_id in restricted:
            continue
        nodes = {
            f"node:{item['id']}"
            for item in snapshot_subjects(payload.get("context_snapshot"))
            if item["kind"] == "node" and item["subject_key"] in selected
        }
        result.update(
            (item["id"], item) for item in payload.get("attachments", []) if item["id"] in nodes
        )
    for subject in snapshot_subjects(snapshot):
        if subject["subject_key"] in selected:
            for media in subject.get("media", []):
                result[media["id"]] = media
    if delegated is not None:
        allowed = {reference["ref_id"] for reference in delegated.get("resources", [])}
        result = {key: value for key, value in result.items() if key in allowed}
    return result


def catalog_locked(session, conversation, task):
    return [
        attachment_ref(item).model_dump(mode="json")
        for item in attachments_locked(session, conversation, task).values()
    ] + archived_references_locked(session, conversation, task)


def archived_references_locked(session, conversation, task):
    binding = session.info.get("assistant_scope")
    delegated = delegated_inputs_locked(session, *binding) if binding else None
    # 子图只读取本scope明确导出的资源和父调用交接的完整引用。
    scope_clause = (AgentOperation.scope_id == binding[1],) if delegated is not None else ()
    references = [
        reference
        for refs in session.scalars(
            select(AgentOperation.result_refs)
            .join(AgentRun, AgentRun.id == AgentOperation.run_id)
            .where(
                AgentRun.conversation_id == conversation.id,
                AgentRun.initiated_by == conversation.owner_user_id,
                *scope_clause,
            )
        )
        for reference in refs
        if reference.get("source") in {"sandbox_artifact", "sandbox_log"}
        and reference.get("availability") == "available"
    ]
    if delegated is not None:
        session.info.pop("assistant_scope")
        try:
            live_parent = catalog_locked(session, conversation, task)
        finally:
            session.info["assistant_scope"] = binding
        references += [
            reference for reference in delegated.get("resources", []) if reference in live_parent
        ]
    media_ids = {
        int(reference["metadata"]["media_id"])
        for reference in references
        if reference["kind"] in {"image", "audio", "video"}
    }
    if not media_ids:
        return references
    media_locators = dict(
        session.execute(
            select(MediaFile.id, MediaFile.storage_locator).where(
                MediaFile.id.in_(media_ids),
                MediaFile.scope_user_id == conversation.owner_user_id,
                MediaFile.project_id.is_(None),
            )
        ).all()
    )
    return [
        reference
        for reference in references
        if reference["kind"] not in {"image", "audio", "video"}
        or media_locators.get(int(reference["metadata"]["media_id"])) == reference["uri"]
    ]


def authorized_reference_locked(session, conversation, task, reference):
    from short_drama.agent.resources.contracts import ResourceRef

    candidates = catalog_locked(session, conversation, task)
    for candidate in candidates:
        if candidate == reference.model_dump(mode="json"):
            return ResourceRef.model_validate(candidate)
    raise NotFound("资源引用不属于当前会话或授权任务范围")
