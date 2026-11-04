"""Authenticated messages, reviews and cancellation; execution belongs to workers."""

from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select

from short_drama.agent.core.access import LeaseLost, lock_run, may_decide
from short_drama.agent.core.events import (
    budget_limits,
    initial_usage,
    public_event_payload,
)
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.core.state import (
    failure_message,
    finish_locked,
    mark_scheduled,
    safe_error,
    wait_locked,
)
from short_drama.agent.runtime.events import (
    finish_trace_locked,
    run_state_locked,
    text_parts_for_turn,
)
from short_drama.agent.runtime.lease import ExecutionClaim
from short_drama.agent.session.lifecycle import finalize_deletion_locked
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import require_session_actor
from short_drama.dao.assistant.execution.run import AgentRunDAO
from short_drama.domain.assistant.conversations.models import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
)
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.identity.accounts.user import User, UserSession
from short_drama.schemas.assistant.operations.runtime import (
    ConversationRuntimeState,
    EventRead,
    MessageRead,
    RunRead,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.assistant.conversations.conversation import AgentConversationService
from short_drama.service.assistant.execution.wait_projection import waiting_summaries
from short_drama.service.base import Page, utcnow


def read_runs(session, rows):
    """Batch supplemental public metadata without loading execution checkpoints."""
    if not rows:
        return []
    dao = AgentRunDAO(session)
    stopping = dao.stopping({row.id for row in rows})
    queued = [row for row in rows if row.status == "queued"]
    queue = dao.queue({row.conversation_id for row in queued}) if queued else []
    waits = waiting_summaries(session, {row.id for row in rows} | {row.id for row in queue})
    blockers = {
        row.id: sorted(
            (
                item
                for item in queue
                if item.conversation_id == row.conversation_id
                and item.id != row.id
                and (item.status != "queued" or item.id < row.id)
            ),
            key=lambda item: (item.status == "queued", item.id),
        )
        for row in queued
    }
    return [
        read_run(
            session,
            row,
            stopping=row.id in stopping,
            queue_position=len(blockers.get(row.id, [])),
            blocker=next(iter(blockers.get(row.id, [])), None),
            waits=waits,
        )
        for row in rows
    ]


def read_run(session, run, *, queue_position=None, stopping=None, blocker=None, waits=None):
    if queue_position is None:
        return read_runs(session, [run])[0]
    if stopping is None:
        stopping = run.id in AgentRunDAO(session).stopping({run.id})
    summary = (waits or {}).get(blocker.id if blocker else run.id)
    reason = summary["message"] if summary else "等待前序任务" if queue_position else None
    if blocker:
        reason = f"等待前序任务「{blocker.goal or '创作任务'}」"
        if summary:
            reason += f"：{summary['message']}；可处理待答问题或停止该任务后继续。"
    return RunRead(
        id=run.id,
        task_id=getattr(run, "task_id", None),
        conversation_id=run.conversation_id,
        status=run.status,
        phase=run.phase,
        row_version=run.row_version,
        mode=(run.checkpoint.get("authorization") or {}).get("mode", "discuss"),
        model_config_id=run.model_config_id,
        model_name=run.config_snapshot.get("name", "文本模型"),
        error={
            **safe_error(run.error.get("code")),
            "message": failure_message(
                run.error.get("code"), accepted_unknown=run.error.get("accepted_unknown") is True
            ),
            **({"accepted_unknown": True} if run.error.get("accepted_unknown") is True else {}),
        }
        if run.error
        else None,
        usage={key: (run.usage or {}).get(key, 0) for key in initial_usage()},
        budget={key: (run.budget or {}).get(key, 0) for key in budget_limits()},
        awaiting_artifact_ids=run.checkpoint.get("awaiting_artifacts", []),
        created_at=run.created_at,
        updated_at=run.updated_at,
        finished_at=run.finished_at,
        queue_position=queue_position,
        waiting_reason=reason,
        wait_summary=summary,
        blocking_run_id=blocker.id if blocker else None,
        blocking_task_id=blocker.task_id if blocker else None,
        stopping=stopping,
    )


class AgentRunService(AgentConversationService):
    def __init__(self, session, settings, storage=None, *, tool_lookup=None):
        super().__init__(session, settings)
        self._storage = storage
        self.tool_lookup = tool_lookup

    def claim(self, run_id: int, message_version: int) -> ExecutionClaim | None:
        from short_drama.agent.core.events import TERMINAL, append_event
        from short_drama.agent.runtime.claims import preceding_execution
        from short_drama.agent.task.queries import task_for_run
        from short_drama.agent.task.state import pending_task_locked
        from short_drama.agent.tools.registry import available_specs
        from short_drama.service.assistant.execution.scope import root_scope_locked

        if not self.settings.agent_enabled:
            return None
        with self.session.begin():
            rows = lock_run(self.session, run_id)
            if rows is None:
                return None
            project, conversation, run = rows
            now = utcnow()
            if (
                run.status not in {"queued", "running", "waiting_generation", "waiting_input"}
                or run.message_version != message_version
                or run.message_status not in {"pending", "publishing", "published"}
                or run.lease_token is not None
                and run.message_status != "publishing"
                or run.next_run_at is None
                or run.next_run_at > now
            ):
                return None
            task = task_for_run(self.session, run)
            if run.cancel_requested or not may_decide(self.session, project, conversation, run):
                self._finish_interrupted_locked(
                    conversation,
                    task,
                    run,
                    "cancelled",
                    {"code": "agent_stopped" if run.cancel_requested else "access_revoked"},
                )
                return None
            if preceding_execution(self.session, run) is not None:
                return None
            if task.control_state == "stop_pending":
                from short_drama.agent.task.controls import apply_controls_locked

                apply_controls_locked(
                    self.session,
                    conversation,
                    task,
                    run,
                )
                if run.status in TERMINAL or run.cancel_requested:
                    return None
            root = root_scope_locked(self.session, run)
            names = (run.checkpoint or {}).get("permitted_tool_names")
            if names is None:
                specs = available_specs(pending_task_locked(self.session, task), media_enabled=True)
                names = [spec.name for spec in specs]
                run.config_snapshot = {
                    **run.config_snapshot,
                    "assistant_settings": {
                        **run.config_snapshot["assistant_settings"],
                        "tool_descriptions": {spec.name: spec.description for spec in specs},
                    },
                }
            run.checkpoint = {
                **run.checkpoint,
                "permitted_tool_names": names,
                "runtime": {"active_since": now.isoformat()},
            }
            if run.status == "queued":
                append_event(
                    self.session,
                    conversation,
                    "run.started",
                    {"run_id": str(run.id)},
                    run_id=run.id,
                )
            run.status, run.phase, run.message_status = "running", "model", "idle"
            run.lease_token = uuid4().hex
            run.lease_until = now + timedelta(seconds=self.settings.agent_lease_seconds)
            run.started_at, run.updated_at = run.started_at or now, now
            run_state_locked(self.session, conversation, run)
            task.status, task.phase = "running", "deciding"
            task.updated_at, task.row_version = now, task.row_version + 1
            return ExecutionClaim(
                run_id=run.id,
                task_id=task.id,
                owner_id=run.initiated_by,
                scope_id=root.id,
                version=run.message_version,
                token=run.lease_token,
                snapshot=deepcopy(run.config_snapshot),
                budget=deepcopy(run.budget),
                tool_names=tuple(names),
                lease_until=run.lease_until,
                discussion_revision=bool(run.checkpoint.get("discussion_revision")),
                pending_input=(
                    not run.checkpoint.get("discussion_revision")
                    and task.input_revision > task.applied_input_revision
                ),
            )

    def execution_active(self, claim: ExecutionClaim) -> bool:
        """One short non-locking control read, independent of the renewal transaction."""
        with self.session.begin():
            return AgentRunDAO(self.session).execution_active(claim)

    def renew(self, claim: ExecutionClaim) -> datetime | bool:
        from short_drama.agent.task.queries import task_for_run

        with self.session.begin():
            rows = lock_run(self.session, claim.run_id)
            if rows is None:
                return False
            project, conversation, run = rows
            if not owns_execution(run, claim.fence) or run.lease_token is None:
                return False
            task = task_for_run(self.session, run)
            if (
                not self.settings.agent_enabled
                or run.cancel_requested
                or task.control_state == "stop_pending"
                or not may_decide(self.session, project, conversation, run)
            ):
                return False
            run.lease_until = utcnow() + timedelta(seconds=self.settings.agent_lease_seconds)
            return run.lease_until

    def finish(
        self,
        claim: ExecutionClaim,
        *,
        status: str,
        content: str = "",
        model_call_id: str | None = None,
        error=None,
    ):
        from short_drama.agent.core.events import append_event
        from short_drama.agent.core.items import KIND_REPLY, allocate_item_seq
        from short_drama.agent.task.queries import task_for_run
        from short_drama.utils.snowflake import next_id

        with self.session.begin():
            rows = lock_run(self.session, claim.run_id)
            if rows is None:
                return False
            project, conversation, run = rows
            if not run.lease_token or not owns_execution(run, claim.fence):
                return False
            task = task_for_run(self.session, run)
            cancellation = self._cancellation_locked(project, conversation, task, run)
            if cancellation:
                status, content, error = "cancelled", "", cancellation
            if status != "succeeded":
                return self._finish_interrupted_locked(conversation, task, run, status, error)
            if not claim.discussion_revision and task.input_revision > task.applied_input_revision:
                # 与消息受理共用同一把锁；已完成图之外的新输入交给同 Run 下一投递。
                return mark_scheduled(run, phase="model")
            if status == "succeeded":
                if run.checkpoint.get("discussion_revision"):
                    from short_drama.agent.task.discussion import replace_reply_locked

                    replace_reply_locked(
                        self.session, conversation, run, content, model_call_id=model_call_id
                    )
                else:
                    from short_drama.dao.assistant.execution.model_ledger import AgentModelLedgerDAO

                    turn = (
                        AgentModelLedgerDAO(self.session, run.id).call(model_call_id)
                        if model_call_id
                        else None
                    )
                    message = AgentMessage(
                        id=next_id(),
                        conversation_id=conversation.id,
                        task_id=run.task_id,
                        seq=conversation.next_message_seq,
                        item_seq=allocate_item_seq(conversation),
                        role="assistant",
                        kind=KIND_REPLY,
                        content=content,
                        references=[],
                        artifacts=[],
                        created_at=utcnow(),
                    )
                    conversation.next_message_seq += 1
                    self.session.add(message)
                    append_event(
                        self.session,
                        conversation,
                        "message.created",
                        {
                            "id": str(message.id),
                            "run_id": str(run.id),
                            "seq": message.seq,
                            "item_seq": message.item_seq,
                            "kind": message.kind,
                            "role": "assistant",
                            "content": content,
                            "artifacts": [],
                            "text_parts": text_parts_for_turn(
                                self.session,
                                conversation,
                                run,
                                turn.id if turn is not None else None,
                                content,
                            ),
                        },
                        run_id=run.id,
                    )
            finished = finish_locked(self.session, conversation, run, status, error)
            finish_trace_locked(self.session, conversation, run)
            finalize_deletion_locked(self.session, conversation)
            return finished

    def wait(self, claim: ExecutionClaim):
        from short_drama.agent.task.queries import task_for_run

        with self.session.begin():
            rows = lock_run(self.session, claim.run_id)
            if rows is None or not rows[2].lease_token or not owns_execution(rows[2], claim.fence):
                raise LeaseLost()
            project, conversation, run = rows
            task = task_for_run(self.session, run)
            cancellation = self._cancellation_locked(project, conversation, task, run)
            if cancellation:
                return self._finish_interrupted_locked(
                    conversation, task, run, "cancelled", cancellation
                )
            from short_drama.dao.assistant.execution.waiting import AgentWaitingDAO
            from short_drama.service.assistant.execution.waiting import schedule_ready_locked

            pending = [
                operation
                for operation in AgentWaitingDAO(self.session).for_run(run.id)
                if not operation.wait_state["consumed"] and not operation.finished_at
            ]
            user_input = any(operation.wait_state["kind"] == "user_input" for operation in pending)
            task.status = task.phase = "waiting_input" if user_input else "waiting_media"
            task.row_version += 1
            task.updated_at = utcnow()
            wait_locked(
                self.session,
                conversation,
                run,
                "waiting_input" if user_input else "waiting_generation",
            )
            run_state_locked(self.session, conversation, run)
            schedule_ready_locked(self.session, conversation, task, run)
            return True

    def _cancellation_locked(self, project, conversation, task, run):
        if run.cancel_requested or task.control_state == "stop_pending":
            return {"code": "agent_stopped"}
        if not may_decide(self.session, project, conversation, run):
            return {"code": "access_revoked"}
        return None

    def _finish_interrupted_locked(self, conversation, task, run, status, error):
        from short_drama.agent.task.controls import apply_controls_locked
        from short_drama.service.assistant.execution.interruptions import settle_interrupted_locked

        settle_interrupted_locked(
            self.session, conversation, run, (error or {}).get("code", "agent_stopped")
        )
        if task.control_state == "stop_pending":
            apply_controls_locked(
                self.session,
                conversation,
                task,
                run,
            )
        finished = finish_locked(self.session, conversation, run, status, error)
        finish_trace_locked(self.session, conversation, run)
        finalize_deletion_locked(self.session, conversation)
        return finished

    @property
    def storage(self):
        from short_drama.service.media.files.storage import StorageService
        from short_drama.storage.minio import MinioStorage

        if not isinstance(self._storage, StorageService):
            self._storage = StorageService(
                self._storage or MinioStorage(self.settings), self.settings
            )
        return self._storage

    def _message_event_metadata(self, rows):
        if not rows:
            return {}
        message_ids = [str(row.id) for row in rows]
        events = self.session.execute(
            select(AgentEvent.seq, AgentEvent.run_id, AgentEvent.event_type, AgentEvent.payload)
            .where(
                AgentEvent.conversation_id == rows[0].conversation_id,
                AgentEvent.event_type.in_(
                    ["message.created", "message.updated", "task.input.received"]
                ),
                or_(
                    and_(
                        AgentEvent.event_type.in_(["message.created", "message.updated"]),
                        AgentEvent.payload["id"].as_string().in_(message_ids),
                    ),
                    and_(
                        AgentEvent.event_type == "task.input.received",
                        AgentEvent.payload["message_id"].as_string().in_(message_ids),
                    ),
                ),
            )
            .order_by(AgentEvent.seq)
        )
        result = {}
        for seq, run_id, event_type, payload in events:
            message_id = (
                payload.get("id")
                if event_type != "task.input.received"
                else payload.get("message_id")
            )
            if message_id:
                result[str(message_id)] = {
                    "event_seq": seq,
                    "run_id": run_id,
                    "text_parts": (
                        payload.get("text_parts", [])
                        if payload.get("content")
                        == next(
                            (
                                message.content
                                for message in rows
                                if str(message.id) == str(message_id)
                            ),
                            None,
                        )
                        else []
                    ),
                }
        return result

    def _message_read(self, row, event_metadata=None):
        from short_drama.domain import MediaFile

        references = deepcopy(row.references)
        for reference in references:
            if reference.get("type") == "attachment" and reference.get("media_id"):
                media = self.session.get(MediaFile, parse_identifier(reference["media_id"]))
                reference["url"] = (
                    self.storage.download_url(media.storage_locator) if media else None
                )
        return MessageRead(
            id=row.id,
            task_id=row.task_id,
            run_id=(event_metadata or {}).get("run_id"),
            seq=row.seq,
            item_seq=row.item_seq,
            event_seq=(event_metadata or {}).get("event_seq"),
            text_parts=(event_metadata or {}).get("text_parts", []),
            row_version=row.row_version,
            role=row.role,
            kind=row.kind,
            content=row.content,
            interrupted=any(ref.get("type") == "interrupted_response" for ref in references),
            references=references,
            artifacts=row.artifacts,
            created_at=row.created_at,
        )

    def _run(self, identifier, *, lock=False):
        identifier = parse_identifier(identifier)
        parent = self.session.scalar(
            select(AgentRun.conversation_id).where(AgentRun.id == identifier)
        )
        if parent is None:
            raise NotFound("Run does not exist")
        conversation = self._conversation(parent, lock=lock)
        stmt = select(AgentRun).where(
            AgentRun.id == identifier, AgentRun.conversation_id == conversation.id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(stmt)
        if row is None:
            raise NotFound("Run does not exist")
        return conversation, row

    def list_messages(self, identifier, offset=0, limit=50):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            query = select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
            rows = self.session.scalars(
                query.order_by(AgentMessage.seq.desc()).offset(offset).limit(limit)
            ).all()
            event_metadata = self._message_event_metadata(rows)
            total = self.session.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.conversation_id == conversation.id
                )
            )
            return Page(
                items=[
                    self._message_read(row, event_metadata.get(str(row.id)))
                    for row in reversed(rows)
                ],
                total=total,
                offset=offset,
                limit=limit,
            )

    def get_run(self, identifier):
        with self._transaction(read_only=True):
            _, run = self._run(identifier)
            return read_run(self.session, run)

    def runtime_state(self, identifier):
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            rows = sorted(
                AgentRunDAO(self.session).projections(conversation.id, active=True),
                key=lambda row: row.id,
            )
            running = next(
                (
                    row
                    for row in rows
                    if row.status in {"running", "waiting_generation", "waiting_input"}
                ),
                None,
            )
            active = running
            queued = [row for row in rows if row.status == "queued"]
            public = {row.id: row for row in read_runs(self.session, rows)}
            snapshot_cursor = conversation.next_event_seq - 1
            resume_cursor = snapshot_cursor
            if rows:
                first_event = self.session.scalar(
                    select(func.min(AgentEvent.seq)).where(
                        AgentEvent.conversation_id == conversation.id,
                        AgentEvent.run_id.in_([row.id for row in rows]),
                        AgentEvent.seq <= snapshot_cursor,
                    )
                )
                if first_event is not None:
                    resume_cursor = first_event - 1
            return ConversationRuntimeState(
                conversation_id=conversation.id,
                cursor=snapshot_cursor,
                resume_cursor=resume_cursor,
                active_run=public[active.id] if active else None,
                queued_runs=[public[row.id] for row in queued],
            )

    def events(self, identifier, cursor=0, *, verify_session=False):
        with self._transaction():
            actor = self._actor()
            if verify_session:
                actor = require_session_actor(actor)
                user = self.session.get(User, actor.user_id)
                login = self.session.get(UserSession, actor.session_id)
                if (
                    user is None
                    or user.status != "active"
                    or not user.email_verified_at
                    or login is None
                    or login.user_id != actor.user_id
                    or login.revoked_at
                    or login.expires_at <= utcnow()
                ):
                    raise WorkflowError("authentication_required", "Please sign in", 401)
            conversation = self._conversation(identifier)
            rows = self.session.scalars(
                select(AgentEvent)
                .where(AgentEvent.conversation_id == conversation.id, AgentEvent.seq > cursor)
                .order_by(AgentEvent.seq)
                .limit(100)
            ).all()
            if conversation.scope_version == 3:
                ceiling = (
                    self.session.scalar(
                        select(AgentConversation.next_event_seq).where(
                            AgentConversation.id == conversation.id
                        )
                    )
                    - 1
                )
                if (
                    cursor > ceiling
                    or (not rows and cursor < ceiling)
                    or any(row.seq != cursor + index + 1 for index, row in enumerate(rows))
                ):
                    raise WorkflowError(
                        "agent_event_resync_required", "事件游标已失效，请重新读取会话快照", 409
                    )
            return [
                EventRead(
                    seq=row.seq,
                    event_type=row.event_type,
                    run_id=row.run_id,
                    payload=public_event_payload(row.event_type, row.payload),
                    created_at=row.created_at,
                )
                for row in rows
            ]
