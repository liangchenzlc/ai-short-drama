"""Account conversations and logical tasks; workers own execution and tool settlement."""

from copy import deepcopy

from sqlalchemy import func, select

from short_drama.agent.attachment_context import (
    available_attachment_ids,
    task_attachments,
    validate_attachment_budget,
)
from short_drama.agent.model_gateway import AgentGatewayError, select_agent_protocol
from short_drama.agent.task_controls import apply_controls_locked
from short_drama.agent.task_state import new_attempt_locked, read_operations, read_task, task_event
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.agent_task_dao import RESUMABLE_TASKS, AgentTaskDAO
from short_drama.db.access import require_project
from short_drama.domain import Project, ProjectCanvas
from short_drama.domain.agent import AgentConversation, AgentEvent, AgentMessage, AgentRun
from short_drama.domain.agent_memory import AgentMemoryItem
from short_drama.domain.agent_task import (
    AgentOperation,
    AgentTask,
    AgentTaskControl,
    AgentTaskInput,
)
from short_drama.schemas.assistant_task import (
    AssistantTaskMessage,
    GlobalConversationCreate,
    GlobalConversationDelete,
    GlobalConversationPatch,
    OperationDetailRead,
    OperationPage,
    TaskControlAccepted,
    TaskControlRequest,
    TaskMessageAccepted,
    TaskRuntimeState,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.agent_attachment_service import attachment_key, freeze_attachments
from short_drama.service.agent_memory_store import (
    deletion_memory_query,
    deletion_memory_read,
    lock_owner_conversations,
    prepare_deletion_memory_locked,
)
from short_drama.service.agent_model_service import AgentModelService, model_snapshot
from short_drama.service.agent_run_service import AgentRunService, read_run
from short_drama.service.agent_skill_service import freeze_skills
from short_drama.service.assistant_subject_service import (
    freeze_legacy_selection,
    freeze_selection,
    inspect_subjects,
    selection_metadata,
    selection_references,
    subject_directory,
)
from short_drama.service.base import Page, utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


class AssistantTaskService(AgentRunService):
    def _writable(self, identifier):
        conversation = self._conversation(identifier, lock=True)
        if conversation.scope_version != 3:
            raise WorkflowError(
                "assistant_legacy_readonly", "历史对话仅供查看，请新建全局对话", 409
            )
        if conversation.status != "active":
            raise WorkflowError(
                "assistant_conversation_deleting", "对话正在删除，不能继续执行", 409
            )
        return conversation

    def create_global_conversation(self, payload, idempotency_key):
        values = self._payload(GlobalConversationCreate, payload)
        key, request_hash = attachment_key(idempotency_key), digest(values)
        actor = self._actor()
        with self._transaction():
            row = self.session.scalar(
                select(AgentConversation)
                .where(
                    AgentConversation.owner_user_id == actor.user_id,
                    AgentConversation.create_key == key,
                )
                .with_for_update()
            )
            if row is not None:
                if row.scope_version != 3 or row.create_hash != request_hash:
                    raise Conflict("此请求身份已经用于其他对话")
                return self._read_conversation(row)
            now = utcnow()
            row = AgentConversation(
                id=next_id(),
                owner_user_id=actor.user_id,
                project_id=None,
                stage=None,
                subject_type=None,
                subject_id=None,
                task_type=None,
                scope_version=3,
                title=values.get("title", "新对话"),
                status="active",
                row_version=1,
                next_message_seq=1,
                next_event_seq=1,
                fixed_requirements={},
                create_key=key,
                create_hash=request_hash,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            self.session.flush()
            return self._read_conversation(row)

    def list_global_conversations(self, offset=0, limit=20, *, search=None):
        actor = self._actor()
        self.dao.validate_pagination(offset, limit)
        conditions = [
            AgentConversation.owner_user_id == actor.user_id,
            AgentConversation.scope_version == 3,
            AgentConversation.status.in_(("active", "deleting")),
        ]
        if search and search.strip():
            conditions.append(AgentConversation.title.contains(search.strip(), autoescape=True))
        with self._transaction(read_only=True):
            rows = self.session.scalars(
                select(AgentConversation)
                .where(*conditions)
                .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return Page(
                items=[self._read_conversation(row) for row in rows],
                total=self.session.scalar(
                    select(func.count()).select_from(AgentConversation).where(*conditions)
                ),
                offset=offset,
                limit=limit,
            )

    def rename_global_conversation(self, identifier, payload):
        values = self._payload(GlobalConversationPatch, payload)
        with self._transaction():
            row = self._writable(identifier)
            if row.row_version != values["row_version"]:
                raise Conflict("对话已变化，请重新读取后修改标题")
            row.title, row.updated_at = values["title"], utcnow()
            row.row_version += 1
            self.session.flush()
            return self._read_conversation(row)

    def historical_conversations(self, offset=0, limit=20, *, search=None):
        actor = self._actor()
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conditions = [
                AgentConversation.owner_user_id == actor.user_id,
                AgentConversation.scope_version < 3,
            ]
            if search and search.strip():
                conditions.append(AgentConversation.title.contains(search.strip(), autoescape=True))
            rows = self.session.scalars(
                select(AgentConversation)
                .where(*conditions)
                .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return Page(
                items=[self._read_conversation(row) for row in rows],
                total=self.session.scalar(
                    select(func.count()).select_from(AgentConversation).where(*conditions)
                ),
                offset=offset,
                limit=limit,
            )

    def deletion_memory_preview(self, identifier, offset=0, limit=30):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            if conversation.scope_version != 3 or conversation.status != "active":
                raise Conflict("此对话已停止使用，请核对删除状态")
            query = deletion_memory_query(conversation)
            rows = self.session.scalars(
                query.order_by(AgentMemoryItem.id).offset(offset).limit(limit)
            ).all()
            return {
                "conversation_id": str(conversation.id),
                "row_version": str(conversation.row_version),
                "items": [deletion_memory_read(self.session, row, conversation) for row in rows],
                "total": self.session.scalar(select(func.count()).select_from(query.subquery())),
                "offset": offset,
                "limit": limit,
            }

    def delete_global_conversation(self, identifier, payload, idempotency_key):
        from short_drama.agent.run_events import append_event
        from short_drama.agent.session_lifecycle import finalize_deletion_locked

        values = self._payload(GlobalConversationDelete, payload)
        key, request_hash = attachment_key(idempotency_key), digest(values)
        with self._transaction():
            actor = self._actor()
            lock_owner_conversations(self.session, actor.user_id, include_deleting=True)
            identifier = parse_identifier(identifier)
            conversation = self.session.scalar(
                select(AgentConversation)
                .where(
                    AgentConversation.id == identifier,
                    AgentConversation.owner_user_id == actor.user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True, assistant_deletion_receipt_id=identifier)
            )
            if conversation is None:
                raise NotFound("对话不存在")
            if conversation.scope_version != 3:
                raise WorkflowError("assistant_legacy_readonly", "历史对话仅供查看", 409)
            deletion = conversation.fixed_requirements.get("deletion")
            if deletion is not None:
                if deletion["key"] != key or deletion["hash"] != request_hash:
                    raise Conflict("此对话已经申请删除，请核对当前状态")
                return self._read_conversation(conversation, last_status=None, last_message="")
            if conversation.row_version != values["row_version"]:
                raise Conflict("对话已变化，请核对当前任务后删除")
            prepare_deletion_memory_locked(
                self.session, conversation, values.get("memory_selections", [])
            )
            conversation.status = "deleting"
            conversation.row_version += 1
            conversation.fixed_requirements = {
                "deletion": {
                    "key": key,
                    "hash": request_hash,
                    "clear_derived_memory": values.get("clear_derived_memory", False),
                    "memory_selections": values.get("memory_selections", []),
                }
            }
            append_event(
                self.session,
                conversation,
                "conversation.deleting",
                {
                    "id": str(conversation.id),
                    "state_version": str(conversation.row_version),
                },
            )
            dao = AgentTaskDAO(self.session)
            tasks = self.session.scalars(
                select(AgentTask)
                .where(
                    AgentTask.conversation_id == conversation.id,
                    AgentTask.status.in_(("queued", "running", "waiting_media")),
                )
                .order_by(AgentTask.id)
                .with_for_update()
            ).all()
            for task in tasks:
                run = dao.latest_run(task.id)
                active = dao.active_operation(task.id)
                task.control_seq += 1
                task.control_state = "delete_pending"
                task.row_version += 1
                self.session.add(
                    AgentTaskControl(
                        id=next_id(),
                        task_id=task.id,
                        seq=task.control_seq,
                        kind="delete",
                        request_key=key,
                        request_hash=request_hash,
                        requested_after_operation_id=active.id if active else None,
                        created_at=utcnow(),
                    )
                )
                self.session.flush()
                apply_controls_locked(self.session, conversation, task, run)
            self.session.flush()
            finalize_deletion_locked(self.session, conversation)
            self.session.flush()
            return self._read_conversation(conversation)

    def _validate_bindings(self, inputs):
        if inputs.context is not None:
            require_project(self.session, inputs.context.project_id)
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
        elif "context" in inputs.model_fields_set:
            payload["context_snapshot"] = freeze_legacy_selection(
                self.session, conversation.owner_user_id, inputs.context
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
        if target and target.get("mode") == "standard":
            from short_drama.service.assistant_dialogue_service import sound_document_binding

            payload["sound_write_binding"] = sound_document_binding(
                self.session, target["project_id"], lock=True
            )
        now = utcnow()
        message = AgentMessage(
            id=next_id(),
            conversation_id=conversation.id,
            task_id=task.id,
            seq=conversation.next_message_seq,
            role="user",
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
        from short_drama.agent.skill_contracts import activations

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
        if sum(len(item["instructions"].encode("utf-8")) for item in result) > 131072:
            raise WorkflowError("agent_skills_too_large", "已加载技能正文合计不能超过 128 KiB", 422)
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

    def send_task_message(self, identifier, payload, idempotency_key):
        inputs = AssistantTaskMessage.model_validate(self._payload(AssistantTaskMessage, payload))
        key, request_hash = attachment_key(idempotency_key), digest(inputs.model_dump(mode="json"))
        dao = AgentTaskDAO(self.session)
        with self._transaction():
            conversation = self._writable(identifier)
            existing = self.session.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.conversation_id == conversation.id,
                    AgentMessage.idempotency_key == key,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.request_hash != request_hash or existing.task_id is None:
                    raise Conflict("此请求身份已经用于其他消息")
                task = dao.task(existing.task_id, lock=True)
                run = dao.latest_run(task.id)
                return self._accepted(conversation, task, run, existing)
            if inputs.task_id is not None:
                task = dao.task(inputs.task_id, conversation_id=conversation.id, lock=True)
                if task is None:
                    raise NotFound("任务不存在或不属于此对话")
                if task.status == "succeeded":
                    raise WorkflowError(
                        "assistant_task_finished", "此任务已完成，请发起新任务", 409
                    )
            else:
                task = None if inputs.new_task else dao.selected_task(conversation.id)
            previous = dao.latest_run(task.id) if task else None
            if task is None:
                model = AgentModelService(self.session, self.settings).select_model(
                    inputs.model_config_id
                )
                snapshot = model_snapshot(model)
                try:
                    select_agent_protocol(snapshot)
                except AgentGatewayError:
                    raise WorkflowError(
                        "unsupported_assistant_protocol", "此模型协议不支持创作助手", 422
                    ) from None
                now = utcnow()
                task = AgentTask(
                    id=next_id(),
                    conversation_id=conversation.id,
                    goal=inputs.content,
                    status="queued",
                    phase="at_boundary",
                    row_version=1,
                    plan_revision=1,
                    input_revision=0,
                    applied_input_revision=0,
                    control_seq=0,
                    applied_control_seq=0,
                    control_state="none",
                    execution_target=None,
                    memory_project_ids=[],
                    context_snapshot=None,
                    selected_skills=[],
                    effective_state={},
                    media_limits=inputs.media_limits.model_dump(),
                    media_usage={},
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(task)
                self.session.flush()
            else:
                model, snapshot = None, previous.config_snapshot
                if previous.checkpoint.get("discussion_revision"):
                    raise WorkflowError(
                        "assistant_discussion_readonly",
                        "历史重答只处理原讨论，请使用独立新任务发送新要求",
                        409,
                    )
                if (
                    inputs.model_config_id is not None
                    and inputs.model_config_id != previous.model_config_id
                ):
                    raise WorkflowError(
                        "assistant_model_frozen", "当前任务使用已固定的模型，请新建任务切换", 409
                    )
            message = self._input_locked(conversation, task, inputs, key, request_hash, snapshot)
            run = previous or new_attempt_locked(
                self.session, conversation, task, message, model.id, snapshot
            )
            self.session.flush()
            return self._accepted(conversation, task, run, message)

    def _accepted(self, conversation, task, run, message):
        return TaskMessageAccepted(
            message=self._message_read(message),
            task=read_task(self.session, task),
            run=read_run(self.session, run),
            cursor=conversation.next_event_seq - 1,
        )

    def revise_discussion(self, identifier, message_id, payload, idempotency_key):
        from short_drama.service.assistant_history_service import revise_discussion

        return revise_discussion(self, identifier, message_id, payload, idempotency_key)

    def discussion_eligibility(self, identifier, message_id):
        from short_drama.service.assistant_history_service import eligibility

        return eligibility(self, identifier, message_id)

    def discussion_revisions(self, identifier, message_id, offset=0, limit=30):
        from short_drama.service.assistant_history_service import revisions

        return revisions(self, identifier, message_id, offset, limit)

    def _task(self, identifier):
        identifier = parse_identifier(identifier)
        parent = self.session.scalar(
            select(AgentTask.conversation_id).where(AgentTask.id == identifier)
        )
        if parent is None:
            raise NotFound("任务不存在")
        conversation = self._writable(parent)
        task = AgentTaskDAO(self.session).task(
            identifier, conversation_id=conversation.id, lock=True
        )
        return conversation, task

    def control_task(self, identifier, payload, idempotency_key):
        values = TaskControlRequest.model_validate(self._payload(TaskControlRequest, payload))
        key, request_hash = attachment_key(idempotency_key), digest(values.model_dump(mode="json"))
        dao = AgentTaskDAO(self.session)
        with self._transaction():
            conversation, task = self._task(identifier)
            existing = self.session.scalar(
                select(AgentTaskControl)
                .where(AgentTaskControl.task_id == task.id, AgentTaskControl.request_key == key)
                .with_for_update()
            )
            run = dao.latest_run(task.id)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise Conflict("此请求身份已经用于其他控制操作")
                return self._control_accepted(conversation, task, run, existing)
            if run.checkpoint.get("discussion_revision") and (
                values.kind == "redirect" or values.content is not None
            ):
                raise WorkflowError(
                    "assistant_discussion_readonly", "历史重答不能改向执行，请发送独立新任务", 409
                )
            if task.status == "succeeded":
                raise WorkflowError("assistant_task_finished", "任务已经完成", 409)
            if values.kind == "resume":
                if values.expected_control_seq != task.control_seq:
                    raise Conflict("停止或暂停状态已变化，请核对当前任务后继续")
                if task.status not in RESUMABLE_TASKS and task.control_state != "stop_pending":
                    raise WorkflowError("assistant_task_not_paused", "任务尚未停止或暂停", 409)
            source = None
            if values.content is not None or values.kind == "resume":
                source = AgentMessage(
                    id=next_id(),
                    conversation_id=conversation.id,
                    task_id=task.id,
                    seq=conversation.next_message_seq,
                    role="user",
                    content=values.content or "继续任务",
                    references=[],
                    artifacts=[],
                    created_at=utcnow(),
                )
                conversation.next_message_seq += 1
                self.session.add(source)
                self.session.flush()
                if values.content is not None:
                    task.input_revision += 1
                    self.session.add(
                        AgentTaskInput(
                            id=next_id(),
                            task_id=task.id,
                            message_id=source.id,
                            input_revision=task.input_revision,
                            payload={},
                            created_at=utcnow(),
                        )
                    )
            active = dao.active_operation(task.id)
            task.control_seq += 1
            task.row_version += 1
            control = AgentTaskControl(
                id=next_id(),
                task_id=task.id,
                seq=task.control_seq,
                kind=values.kind,
                request_key=key,
                request_hash=request_hash,
                expected_control_seq=values.expected_control_seq,
                source_message_id=source.id if source else None,
                requested_after_operation_id=active.id if active else None,
                created_at=utcnow(),
            )
            self.session.add(control)
            if values.kind == "stop":
                task.control_state = "stop_pending"
            elif values.kind == "redirect" and task.control_state != "stop_pending":
                task.control_state = "redirect_pending"
            self.session.flush()
            task_event(
                self.session,
                conversation,
                task,
                "control.requested",
                run=run,
                control_id=str(control.id),
                control_seq=str(control.seq),
                kind=values.kind,
            )
            apply_controls_locked(self.session, conversation, task, run)
            self.session.flush()
            return self._control_accepted(conversation, task, dao.latest_run(task.id), control)

    def _control_accepted(self, conversation, task, run, control):
        return TaskControlAccepted(
            control_id=control.id,
            task=read_task(self.session, task),
            run=read_run(self.session, run),
            cursor=conversation.next_event_seq - 1,
        )

    def task_runtime_state(self, identifier, *, before_task_id=None, limit=20):
        self.dao.validate_pagination(0, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            query = select(AgentTask).where(AgentTask.conversation_id == conversation.id)
            if before_task_id is not None:
                query = query.where(AgentTask.id < parse_identifier(before_task_id))
            tasks = self.session.scalars(query.order_by(AgentTask.id.desc()).limit(limit + 1)).all()
            selected = tasks[:limit]
            task_ids = [task.id for task in selected]
            operations = (
                self.session.scalars(
                    select(AgentOperation)
                    .where(AgentOperation.task_id.in_(task_ids))
                    .order_by(AgentOperation.id)
                    .limit(201)
                ).all()
                if selected
                else []
            )
            runs = (
                self.session.scalars(
                    select(AgentRun).where(AgentRun.task_id.in_(task_ids)).order_by(AgentRun.id)
                ).all()
                if task_ids
                else []
            )
            messages = self.session.scalars(
                select(AgentMessage)
                .where(AgentMessage.conversation_id == conversation.id)
                .order_by(AgentMessage.seq.desc())
                .limit(50)
            ).all()
            cursor = conversation.next_event_seq - 1
            first_delta = self.session.scalar(
                select(func.min(AgentEvent.seq)).where(
                    AgentEvent.conversation_id == conversation.id,
                    AgentEvent.run_id.in_(
                        [run.id for run in runs if run.status in {"queued", "running"}]
                    ),
                    AgentEvent.event_type == "assistant.delta",
                    AgentEvent.seq <= cursor,
                )
            )
            inputs = (
                self.session.scalars(
                    select(AgentTaskInput)
                    .where(AgentTaskInput.task_id.in_(task_ids))
                    .order_by(AgentTaskInput.task_id, AgentTaskInput.input_revision)
                ).all()
                if task_ids
                else []
            )
            return TaskRuntimeState(
                conversation_id=conversation.id,
                snapshot_cursor=cursor,
                resume_cursor=first_delta - 1 if first_delta is not None else cursor,
                messages={
                    "items": [self._message_read(message) for message in reversed(messages)],
                    "total": self.session.scalar(
                        select(func.count())
                        .select_from(AgentMessage)
                        .where(AgentMessage.conversation_id == conversation.id)
                    ),
                    "offset": 0,
                    "limit": 50,
                },
                runs=[read_run(self.session, run) for run in runs],
                tasks=[read_task(self.session, task) for task in selected],
                operations=read_operations(self.session, operations[:200]),
                inputs=[
                    {
                        "task_id": str(item.task_id),
                        "message_id": str(item.message_id),
                        "input_revision": str(item.input_revision),
                        "applied_plan_revision": (
                            str(item.applied_plan_revision) if item.applied_plan_revision else None
                        ),
                    }
                    for item in inputs
                ],
                has_more_tasks=len(tasks) > limit,
                has_more_operations=len(operations) > 200,
            )

    def operation_page(self, identifier, *, after_id=None, limit=50):
        self.dao.validate_pagination(0, limit)
        with self._transaction(read_only=True):
            task = AgentTaskDAO(self.session).task(parse_identifier(identifier))
            if task is None:
                raise NotFound("任务不存在")
            conversation = self._conversation(task.conversation_id)
            query = select(AgentOperation).where(AgentOperation.task_id == task.id)
            if after_id is not None:
                query = query.where(AgentOperation.id > parse_identifier(after_id))
            rows = self.session.scalars(query.order_by(AgentOperation.id).limit(limit + 1)).all()
            return OperationPage(
                items=read_operations(self.session, rows[:limit]),
                has_more=len(rows) > limit,
                next_after_id=rows[limit - 1].id if len(rows) > limit else None,
                snapshot_cursor=conversation.next_event_seq - 1,
            )

    def operation_detail(self, identifier):
        with self._transaction(read_only=True):
            row = self.session.get(AgentOperation, parse_identifier(identifier))
            if row is None:
                raise NotFound("操作不存在")
            task = AgentTaskDAO(self.session).task(row.task_id)
            self._conversation(task.conversation_id)
            return OperationDetailRead(
                **read_operations(self.session, [row])[0].model_dump(), result=row.result
            )

    def undo_preview(self, identifier):
        from short_drama.service.assistant_undo_service import preview

        return preview(self, identifier)

    def undo_batch(self, identifier, payload, idempotency_key):
        from short_drama.service.assistant_undo_service import apply

        return apply(self, identifier, payload, idempotency_key)
