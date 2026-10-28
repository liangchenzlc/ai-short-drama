"""Task-owned resource indexes; directory reads never fetch storage entities."""

from sqlalchemy import select

from short_drama.agent.context.attachments import available_attachment_ids, task_attachments
from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.resources.resolver import attachment_ref
from short_drama.domain.agent_task import AgentTaskInput
from short_drama.service.assistant_subject_service import selection_metadata


def attachments_locked(session, conversation, task):
    restricted, _ = restricted_messages(session, conversation.id)
    attached = task_attachments(session, task, restricted_ids=restricted)
    available = available_attachment_ids(session, conversation, attached)
    result = {key: item for key, item in attached.items() if key in available}
    metadata = selection_metadata(session, conversation.owner_user_id, task.context_snapshot)
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
    for subject in snapshot_subjects(task.context_snapshot):
        if subject["subject_key"] in selected:
            for media in subject.get("media", []):
                result[media["id"]] = media
    return result


def catalog_locked(session, conversation, task):
    return [
        attachment_ref(item).model_dump(mode="json")
        for item in attachments_locked(session, conversation, task).values()
    ]
