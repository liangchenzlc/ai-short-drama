from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.context.attachments import (
    available_attachment_ids,
    task_attachments,
    validate_attachment_budget,
)
from short_drama.agent.core.items import KIND_REPLY, allocate_item_seq
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.task.state import (
    task_event,
)
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.db.scope.resource_scope import require_project
from short_drama.domain import Project, ProjectCanvas
from short_drama.domain.assistant.conversations.models import (
    AgentMessage,
)
from short_drama.domain.assistant.tasks.task import (
    AgentTaskInput,
)
from short_drama.schemas.assistant.tasks.task import (
    TaskExecutionTarget,
)
from short_drama.service.assistant.context.attachment import freeze_attachments
from short_drama.service.assistant.context.subject import (
    freeze_selection,
    inspect_subjects,
    selection_metadata,
    selection_references,
    subject_directory,
)
from short_drama.service.assistant.tasks.base import TaskServiceBase
from short_drama.service.base import utcnow
from short_drama.service.skills.catalog.versions import freeze_skills
from short_drama.utils.snowflake import next_id


class TaskInputOperations(TaskServiceBase):
    def _project_inputs(self, conversation, task, inputs):
        """Bind new task defaults after replay lookup; accepted tasks keep their destination."""
        if conversation.project_id is None:
            return inputs
        project = self.session.get(Project, conversation.project_id)
        target = task.execution_target if task else None
        if task:
            for item in AgentTaskDAO(self.session).inputs(task):
                target = item.payload.get("execution_target", target)
        frozen = TaskExecutionTarget.model_validate(target) if target else None
        requested = inputs.execution_target
        if requested is not None and (
            requested.project_id != project.id
            or requested.mode != project.workspace_mode
            or frozen is not None
            and requested != frozen
        ):
            raise WorkflowError("scope_mismatch", "任务只能保存到所属项目及原画布", 422)
        if any(identifier != project.id for identifier in inputs.memory_project_ids):
            raise WorkflowError("scope_mismatch", "任务使用所属项目的创作设定", 422)
        target = frozen or requested
        if target is None:
            if project.workspace_mode == "infinite_canvas":
                raise WorkflowError("scope_mismatch", "新任务需要当前画布作为保存目标", 422)
            target = TaskExecutionTarget(mode="standard", project_id=project.id)
        return inputs.model_copy(
            update={"execution_target": target, "memory_project_ids": [project.id]}
        )

    def _validate_bindings(self, inputs):
        for project_id in inputs.memory_project_ids:
            require_project(self.session, project_id)
        target = inputs.execution_target
        if target is not None:
            require_project(self.session, target.project_id)
            project = self.session.get(Project, target.project_id)
            if project.workspace_mode != target.mode:
                raise WorkflowError("scope_mismatch", "写入目标的创作模式不匹配", 422)
            if (
                target.canvas_id is not None
                and self.session.scalar(
                    select(ProjectCanvas.id).where(
                        ProjectCanvas.project_id == target.project_id,
                        ProjectCanvas.source_key == target.canvas_id,
                        ProjectCanvas.archived_at.is_(None),
                    )
                )
                is None
            ):
                raise NotFound("写入画布不存在或无权访问")

    def _input_locked(self, conversation, task, inputs, key, request_hash, snapshot):
        self._validate_bindings(inputs)
        references, attachments, attachment_rows = freeze_attachments(
            self.session, conversation, inputs.attachment_ids, snapshot, inputs.video_audio
        )
        payload = {}
        retained = task_attachments(self.session, task, include_pending=True)
        if "attachment_selection" in inputs.model_fields_set:
            selection = inputs.attachment_selection
            if selection.expected_input_revision != task.input_revision:
                raise WorkflowError(
                    "assistant_selection_changed", "任务附件或输入已变化，请核对后发送", 409
                )
            identifiers = [str(value) for value in selection.retain_ids]
            if not set(identifiers).issubset(retained):
                raise WorkflowError("assistant_attachment_scope", "只能保留本任务已选入的附件", 422)
            payload["retained_attachment_ids"] = identifiers
            retained = {key: value for key, value in retained.items() if key in identifiers}
        if "subject_selection" in inputs.model_fields_set:
            selection = inputs.subject_selection
            if selection.expected_input_revision != task.input_revision:
                raise WorkflowError(
                    "assistant_selection_changed", "任务引用或输入已变化，请核对后发送", 409
                )
            payload["context_snapshot"] = freeze_selection(
                self.session, conversation.owner_user_id, selection, self._pending_selection(task)
            )
        source = payload.get("context_snapshot", self._pending_selection(task))
        if "context_snapshot" in payload:
            references.extend(selection_references(source))
        source_media = [
            media for item in (source or {}).get("subjects", []) for media in item.get("media", [])
        ]
        retained.update((value["id"], value) for value in attachments)
        validate_attachment_budget(retained, source_media)
        payload["attachments"] = attachments
        payload["video_audio"] = inputs.video_audio
        if "skills" in inputs.model_fields_set:
            payload["selected_skills"] = self._freeze_task_skills(conversation, task, inputs.skills)
        for field in ("execution_target", "memory_project_ids", "media_limits"):
            if field in inputs.model_fields_set:
                payload[field] = inputs.model_dump(mode="json")[field]
        target = task.execution_target
        for item in AgentTaskDAO(self.session).inputs(task):
            target = item.payload.get("execution_target", target)
        target = payload.get("execution_target", target)
        if "script_reference" in inputs.model_fields_set:
            from short_drama.agent.tools.script import bound_document

            reference = (
                inputs.script_reference.model_dump(mode="json") if inputs.script_reference else None
            )
            if reference:
                bound_document(self.session, target, reference)
            payload["script_reference"] = reference
        if target and target.get("mode") == "standard":
            from short_drama.service.assistant.operations.dialogue import sound_document_binding

            payload["sound_write_binding"] = sound_document_binding(
                self.session, target["project_id"], lock=True
            )
        now = utcnow()
        message = AgentMessage(
            id=next_id(),
            conversation_id=conversation.id,
            task_id=task.id,
            seq=conversation.next_message_seq,
            item_seq=allocate_item_seq(conversation),
            role="user",
            kind=KIND_REPLY,
            content=inputs.content,
            references=references,
            artifacts=[],
            idempotency_key=key,
            request_hash=request_hash,
            created_at=now,
        )
        conversation.next_message_seq += 1
        task.input_revision += 1
        task.row_version += 1
        task.updated_at = now
        self.session.add(message)
        self.session.flush()
        self.session.add(
            AgentTaskInput(
                id=next_id(),
                task_id=task.id,
                message_id=message.id,
                input_revision=task.input_revision,
                payload=payload,
                created_at=now,
            )
        )
        for attachment in attachment_rows:
            if attachment.attached_message_id is None:
                attachment.attached_message_id = message.id
        task_event(
            self.session,
            conversation,
            task,
            "task.input.received",
            message_id=str(message.id),
            input_revision=str(task.input_revision),
        )
        return message

    def _freeze_task_skills(self, conversation, task, selections):
        from short_drama.agent.skill.contracts import activations

        automatic = [
            item["skill"]
            for item in activations(task)
            if item["active"] and item["source"] == "auto"
        ]
        retained = task.selected_skills
        for item in AgentTaskDAO(self.session).inputs(task):
            if "selected_skills" in item.payload:
                retained = item.payload["selected_skills"]
        frozen = {
            (item["id"], str(item["content_version"])): item for item in [*automatic, *retained]
        }
        added = freeze_skills(
            self.session,
            conversation.owner_user_id,
            [item for item in selections if (item.id, str(item.content_version)) not in frozen],
        )
        frozen.update(((item["id"], str(item["content_version"])), item) for item in added)
        result = [deepcopy(frozen[item.id, str(item.content_version)]) for item in selections]
        if sum(len(item.get("instructions", "").encode("utf-8")) for item in result) > 131072:
            raise WorkflowError("agent_skills_too_large", "已加载技能正文合计不能超过 128 KiB", 422)
        from short_drama.agent.skill.progressive import skill_index

        if task.effective_state.get("engine") == ENGINE_VERSION:
            return [skill_index(item) for item in result]
        return result

    def _pending_selection(self, task):
        snapshot = task.context_snapshot
        for item in AgentTaskDAO(self.session).inputs(task):
            if "context_snapshot" in item.payload:
                snapshot = item.payload["context_snapshot"]
        return snapshot

    def subject_metadata(self, identifier):
        with self._transaction(read_only=True):
            conversation, task = self._task(identifier)
            snapshot = self._pending_selection(task)
            return {
                "task_id": str(task.id),
                "input_revision": str(task.input_revision),
                "applied_input_revision": str(task.applied_input_revision),
                "pending": any(
                    "context_snapshot" in item.payload
                    for item in AgentTaskDAO(self.session).inputs(task)
                ),
                "items": selection_metadata(self.session, conversation.owner_user_id, snapshot),
            }

    def attachment_metadata(self, identifier):
        with self._transaction(read_only=True):
            conversation, task = self._task(identifier)
            selected = task_attachments(self.session, task, include_pending=True)
            available = available_attachment_ids(self.session, conversation, selected)
            return {
                "task_id": str(task.id),
                "input_revision": str(task.input_revision),
                "applied_input_revision": str(task.applied_input_revision),
                "pending": any(
                    item.payload.get("attachments") or "retained_attachment_ids" in item.payload
                    for item in AgentTaskDAO(self.session).inputs(task)
                ),
                "items": [
                    {
                        "id": item["id"],
                        "kind": item["kind"],
                        "name": item["name"],
                        "checksum_sha256": item["checksum_sha256"],
                        "available": item["id"] in available,
                    }
                    for item in selected.values()
                ],
            }

    def subject_directory(self, project_id, kind, query, offset, limit):
        with self._transaction(read_only=True):
            return subject_directory(
                self.session, self._actor().user_id, project_id, kind, query, offset, limit
            )

    def inspect_subjects(self, payload):
        with self._transaction(read_only=True):
            return inspect_subjects(self.session, self._actor().user_id, payload.subjects)
