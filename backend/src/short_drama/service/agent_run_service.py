"""Authenticated messages, reviews and cancellation; execution belongs to workers."""

import hashlib
from copy import deepcopy

from sqlalchemy import func, select

from short_drama.agent.authorization import (
    check_source,
    digest,
    discussion_only,
    freeze_task,
    public_step,
)
from short_drama.agent.state import (
    ACTIVE,
    TERMINAL,
    append_event,
    budget_limits,
    finish_locked,
    initial_usage,
    mark_scheduled,
)
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import AIModelConfig, Asset, Episode, ShotScript
from short_drama.domain.agent import (
    AgentArtifact,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
)
from short_drama.domain.collaboration import User, UserSession
from short_drama.schemas.agent_runtime import (
    ConversationRuntimeState,
    EventRead,
    MessageAccepted,
    MessageCreate,
    MessageRead,
    PlanReviewRead,
    ReviewDecision,
    RunContinue,
    RunRead,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.agent_attachment_service import freeze_attachments
from short_drama.service.agent_conversation_service import (
    AgentConversationService,
    conversation_scope,
)
from short_drama.service.agent_model_service import AgentModelService, model_snapshot
from short_drama.service.agent_skill_service import freeze_skills
from short_drama.service.base import Page, utcnow
from short_drama.utils.snowflake import next_id

_UNSET = object()


def read_run(session, run, *, tool=_UNSET, queue_position=None):
    if queue_position is None:
        queue_position = 0
        if run.status == "queued":
            queue_position = (
                session.scalar(
                    select(func.count(AgentRun.id)).where(
                        AgentRun.conversation_id == run.conversation_id,
                        AgentRun.id != run.id,
                        AgentRun.status.in_(("running", "waiting_generation"))
                        | ((AgentRun.status == "queued") & (AgentRun.id < run.id)),
                    )
                )
                or 0
            )
    if tool is _UNSET:
        tool = session.scalar(
            select(AgentToolCall)
            .where(AgentToolCall.run_id == run.id, AgentToolCall.status == "waiting_review")
            .order_by(AgentToolCall.id)
            .limit(1)
        )
    review = None
    if tool is not None:
        payload = tool.review_payload
        review = PlanReviewRead(
            tool_call_id=tool.id,
            review_version=tool.review_version,
            review_hash=tool.review_hash,
            title=payload["title"],
            summary=payload["summary"],
            steps=[public_step(step) for step in payload["steps"]],
        )
    return RunRead(
        id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        phase=run.phase,
        row_version=run.row_version,
        mode=(run.checkpoint.get("authorization") or {}).get("mode", "discuss"),
        model_config_id=run.model_config_id,
        model_name=run.config_snapshot.get("name", "文本模型"),
        error={"code": run.error.get("code", "agent_failed")} if run.error else None,
        usage={key: (run.usage or {}).get(key, 0) for key in initial_usage()},
        budget={key: (run.budget or {}).get(key, 0) for key in budget_limits()},
        review=review,
        awaiting_artifact_ids=run.checkpoint.get("awaiting_artifacts", []),
        created_at=run.created_at,
        updated_at=run.updated_at,
        finished_at=run.finished_at,
        queue_position=queue_position,
        waiting_reason="等待前序任务" if queue_position else None,
    )


class AgentRunService(AgentConversationService):
    def __init__(self, session, settings, storage=None):
        super().__init__(session, settings)
        self._storage = storage

    @property
    def storage(self):
        from short_drama.service.storage_service import StorageService
        from short_drama.storage.minio import MinioStorage

        if not isinstance(self._storage, StorageService):
            self._storage = StorageService(
                self._storage or MinioStorage(self.settings), self.settings
            )
        return self._storage

    def _message_read(self, row):
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
            seq=row.seq,
            role=row.role,
            content=row.content,
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

    def send_message(self, identifier, payload, idempotency_key):
        values = MessageCreate.model_validate(self._payload(MessageCreate, payload))
        if (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 64
            or not all(
                char.isascii() and (char.isalnum() or char in "_-:") for char in idempotency_key
            )
        ):
            raise WorkflowError("invalid_idempotency_key", "发送消息需要有效的幂等键", 422)
        request_hash = digest(values.model_dump(mode="json"))
        key = hashlib.sha256(idempotency_key.encode()).hexdigest()
        with self._transaction():
            conversation = self._conversation(identifier, lock=True)
            self.check_expected_scope(conversation, values.expected_scope, validate_subject=True)
            existing = self.session.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.conversation_id == conversation.id,
                    AgentMessage.idempotency_key == key,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise Conflict("Idempotency key was used for a different message")
                run = self.session.scalar(
                    select(AgentRun)
                    .where(AgentRun.trigger_message_id == existing.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                return MessageAccepted(
                    message=self._message_read(existing),
                    run=read_run(self.session, run),
                    cursor=conversation.next_event_seq - 1,
                )
            if conversation.status != "active":
                raise WorkflowError("agent_conversation_archived", "请先恢复这段对话", 409)
            preceding = self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.conversation_id == conversation.id,
                    AgentRun.status.in_(ACTIVE),
                )
                .order_by(AgentRun.id)
                .with_for_update()
            ).all()
            actor = self._actor()
            model = AgentModelService(self.session, self.settings).select_model(
                values.model_config_id
            )
            snapshot = model_snapshot(model)
            references, attachments, attachment_rows = freeze_attachments(
                self.session, conversation, values.attachment_ids, snapshot, values.video_audio
            )
            selected_skills = freeze_skills(self.session, actor.user_id, values.skills)
            references.extend(
                {
                    "type": "skill",
                    "id": skill["id"],
                    "name": skill["name"],
                    "content_version": skill["content_version"],
                    "builtin": skill["builtin"],
                }
                for skill in selected_skills
            )
            task = values.task
            narrowed = discussion_only(values.content)
            authorization = {
                "mode": "discuss"
                if values.mode == "discuss"
                or narrowed
                or (values.mode == "auto" and conversation.scope_version != 1)
                else "auto",
                "steps": [],
                "consumed_steps": [],
                "requires_plan": values.mode == "generate" and task is None and not narrowed,
                "user_constraints": values.content,
            }
            if (
                values.mode != "discuss"
                and task is not None
                and not narrowed
                and (
                    task.count == 1 or values.mode == "generate" and conversation.scope_version == 0
                )
            ):
                authorization.update(
                    mode="single",
                    steps=[
                        freeze_task(
                            self.session,
                            conversation,
                            task,
                            step_id="single",
                            owner_user_id=actor.user_id,
                        )
                    ],
                )
            # Previous private chat is included as plain messages, never dangling tool calls.
            recent = self.session.scalars(
                select(AgentMessage)
                .where(AgentMessage.conversation_id == conversation.id)
                .order_by(AgentMessage.seq.desc())
                .limit(30)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            context = [
                {
                    "role": item.role,
                    "content": item.content,
                    "references": [
                        {
                            key: value
                            for key, value in reference.items()
                            if key not in {"url", "storage_locator"}
                        }
                        for reference in item.references
                    ],
                }
                for item in reversed(recent)
            ]
            now = utcnow()
            message = AgentMessage(
                id=next_id(),
                conversation_id=conversation.id,
                seq=conversation.next_message_seq,
                role="user",
                content=values.content,
                references=references,
                artifacts=[],
                idempotency_key=key,
                request_hash=request_hash,
                created_at=now,
            )
            conversation.next_message_seq += 1
            self.session.add(message)
            self.session.flush()
            for attachment in attachment_rows:
                if attachment.attached_message_id is None:
                    attachment.attached_message_id = message.id
            images = sum(
                step["count"] for step in authorization["steps"] if step["kind"] == "image"
            )
            videos = sum(
                step["count"] for step in authorization["steps"] if step["kind"] == "video"
            )
            run = AgentRun(
                id=next_id(),
                conversation_id=conversation.id,
                trigger_message_id=message.id,
                initiated_by=actor.user_id,
                model_config_id=model.id,
                status="queued",
                phase="model",
                row_version=1,
                checkpoint_schema_version=1,
                checkpoint={
                    "authorization": authorization,
                    "history": None,
                    "scope": {
                        "project_id": str(conversation.project_id),
                        "episode_id": str(conversation.episode_id),
                        **(conversation_scope(conversation) or {}),
                    },
                    "supplement_run_id": next(
                        (
                            str(previous.id)
                            for previous in reversed(preceding)
                            if previous.status == "waiting_review"
                            and not (previous.checkpoint or {}).get("awaiting_artifacts")
                        ),
                        None,
                    ),
                    "user_prompt": {
                        "codec": "agent.attachments",
                        "content": values.content,
                        "attachments": attachments,
                        "video_audio": values.video_audio,
                    }
                    if attachments
                    else values.content,
                    "conversation_context": context,
                    "selected_skills": selected_skills,
                    "requested_task": task.model_dump(mode="json") if task else None,
                },
                config_snapshot=snapshot,
                budget=budget_limits(images=images, videos=videos),
                usage=initial_usage(),
                message_status="pending",
                message_version=1,
                next_run_at=now,
                cancel_requested=0,
                created_at=now,
                updated_at=now,
            )
            self.session.add(run)
            self.session.flush()
            append_event(
                self.session,
                conversation,
                "message.created",
                {
                    "id": str(message.id),
                    "run_id": str(run.id),
                    "seq": message.seq,
                    "role": "user",
                    "content": message.content,
                    "references": references,
                },
                run_id=run.id,
            )
            append_event(
                self.session,
                conversation,
                "run.started",
                {"run_id": str(run.id), "status": "queued"},
                run_id=run.id,
            )
            self.session.flush()
            return MessageAccepted(
                message=self._message_read(message),
                run=read_run(
                    self.session,
                    run,
                    queue_position=sum(
                        previous.status != "waiting_review" for previous in preceding
                    ),
                ),
                cursor=conversation.next_event_seq - 1,
            )

    def list_messages(self, identifier, offset=0, limit=50, *, expected_scope=None):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier, expected_scope=expected_scope)
            query = select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
            rows = self.session.scalars(
                query.order_by(AgentMessage.seq.desc()).offset(offset).limit(limit)
            ).all()
            total = self.session.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.conversation_id == conversation.id
                )
            )
            return Page(
                items=[self._message_read(row) for row in reversed(rows)],
                total=total,
                offset=offset,
                limit=limit,
            )

    def list_runs(self, identifier, offset=0, limit=20, *, expected_scope=None):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier, expected_scope=expected_scope)
            rows = self.session.scalars(
                select(AgentRun)
                .where(AgentRun.conversation_id == conversation.id)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            total = self.session.scalar(
                select(func.count(AgentRun.id)).where(AgentRun.conversation_id == conversation.id)
            )
            reviews = {}
            if rows:
                first = (
                    select(func.min(AgentToolCall.id).label("id"))
                    .where(
                        AgentToolCall.run_id.in_([row.id for row in rows]),
                        AgentToolCall.status == "waiting_review",
                    )
                    .group_by(AgentToolCall.run_id)
                    .subquery()
                )
                reviews = {
                    tool.run_id: tool
                    for tool in self.session.scalars(
                        select(AgentToolCall).join(first, AgentToolCall.id == first.c.id)
                    )
                }
            return Page(
                items=[read_run(self.session, row, tool=reviews.get(row.id)) for row in rows],
                total=total,
                offset=offset,
                limit=limit,
            )

    def get_run(self, identifier):
        with self._transaction(read_only=True):
            _, run = self._run(identifier)
            return read_run(self.session, run)

    def runtime_state(self, identifier, expected_scope=None):
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier, expected_scope=expected_scope)
            rows = self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.conversation_id == conversation.id,
                    AgentRun.status.in_(ACTIVE),
                )
                .order_by(AgentRun.id)
            ).all()
            running = next(
                (row for row in rows if row.status in {"running", "waiting_generation"}), None
            )
            active = running or next(
                (row for row in reversed(rows) if row.status == "waiting_review"), None
            )
            queued = [row for row in rows if row.status == "queued"]
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
                active_run=read_run(self.session, active) if active else None,
                queued_runs=[
                    read_run(self.session, row, queue_position=index + (1 if running else 0))
                    for index, row in enumerate(queued)
                ],
            )

    def stop(self, identifier):
        with self._transaction():
            conversation, run = self._run(identifier, lock=True)
            if run.status not in TERMINAL:
                run.cancel_requested = 1
                tools = self.session.scalars(
                    select(AgentToolCall)
                    .where(AgentToolCall.run_id == run.id)
                    .order_by(AgentToolCall.id)
                    .with_for_update()
                ).all()
                for tool in tools:
                    if tool.status not in {"succeeded", "failed", "rejected", "cancelled"}:
                        tool.status, tool.updated_at = "cancelled", utcnow()
                finish_locked(self.session, conversation, run, "cancelled")
            self.session.flush()
            return read_run(self.session, run)

    def review(self, identifier, tool_id, payload):
        values = ReviewDecision.model_validate(payload)
        with self._transaction():
            conversation, run = self._run(identifier, lock=True)
            tool = self.session.scalar(
                select(AgentToolCall)
                .where(
                    AgentToolCall.id == parse_identifier(tool_id), AgentToolCall.run_id == run.id
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if tool is None:
                raise NotFound("Review does not exist")
            if (
                tool.review_version != values.review_version
                or tool.review_hash != values.review_hash
            ):
                raise Conflict("Plan changed; reload before reviewing")
            if tool.review_decision is not None:
                if tool.review_decision != values.decision:
                    raise Conflict("Plan already has a different decision")
                return read_run(self.session, run)
            if (
                run.status != "waiting_review"
                or run.cancel_requested
                or tool.status != "waiting_review"
            ):
                raise Conflict("This plan is no longer awaiting review")
            supplementary = self.session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.conversation_id == conversation.id,
                    AgentRun.id != run.id,
                    AgentRun.status.in_(("queued", "running")),
                )
                .with_for_update()
            ).all()
            if supplementary:
                raise WorkflowError(
                    "agent_review_busy", "正在处理补充消息，请等回复后再审核计划", 409
                )
            if values.decision == "approved":
                for step in tool.review_payload["steps"]:
                    check_source(self.session, step)
                    if "model_snapshot" in step:
                        current = self.session.scalar(
                            select(AIModelConfig)
                            .where(AIModelConfig.id == int(step["model_config_id"]))
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                        frozen = step["model_snapshot"]
                        if (
                            current is None
                            or current.owner_user_id != run.initiated_by
                            or not current.enabled
                            or current.is_deleted
                            or current.row_version != frozen["row_version"]
                        ):
                            raise Conflict("Media model changed; ask for a new plan")
                checkpoint = deepcopy(run.checkpoint)
                checkpoint["authorization"].update(
                    mode="workflow",
                    approved_plan={"tool_call_id": str(tool.id), "hash": tool.review_hash},
                    steps=deepcopy(tool.review_payload["steps"]),
                    consumed_steps=[],
                    requires_plan=False,
                )
                run.checkpoint = checkpoint
                images = sum(
                    step["count"]
                    for step in tool.review_payload["steps"]
                    if step["kind"] == "image"
                )
                videos = sum(
                    step["count"]
                    for step in tool.review_payload["steps"]
                    if step["kind"] == "video"
                )
                run.budget = budget_limits("workflow", images=images, videos=videos)
                tool.status = "succeeded"
                tool.result = {
                    "decision": "approved",
                    "steps": [public_step(step) for step in tool.review_payload["steps"]],
                }
            else:
                checkpoint = deepcopy(run.checkpoint)
                checkpoint["authorization"].update(
                    mode="discuss", steps=[], consumed_steps=[], requires_plan=False
                )
                run.checkpoint = checkpoint
                tool.status, tool.result = (
                    "rejected",
                    {"decision": "rejected", "instruction": "Do not execute this plan."},
                )
            tool.review_decision, tool.reviewed_by, tool.reviewed_at = (
                values.decision,
                self._actor().user_id,
                utcnow(),
            )
            tool.updated_at = utcnow()
            mark_scheduled(run, phase="tools")
            append_event(
                self.session,
                conversation,
                "review.decided",
                {"run_id": str(run.id), "decision": values.decision},
                run_id=run.id,
            )
            self.session.flush()
            return read_run(self.session, run)

    def events(self, identifier, cursor=0, *, verify_session=False, expected_scope=None):
        with self._transaction():
            actor = self._actor()
            if verify_session:
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
            conversation = self._conversation(identifier, expected_scope=expected_scope)
            rows = self.session.scalars(
                select(AgentEvent)
                .where(AgentEvent.conversation_id == conversation.id, AgentEvent.seq > cursor)
                .order_by(AgentEvent.seq)
                .limit(100)
            ).all()
            return [EventRead.model_validate(row) for row in rows]

    def continue_after_adoption(self, identifier, payload):
        values = RunContinue.model_validate(self._payload(RunContinue, payload))
        with self._transaction():
            conversation, run = self._run(identifier, lock=True)
            checkpoint = deepcopy(run.checkpoint)
            previous = checkpoint.get("continued_artifacts", {})
            key = str(values.artifact_id)
            if previous.get(key) == values.artifact_row_version:
                return read_run(self.session, run)
            if (
                run.status != "waiting_review"
                or run.cancel_requested
                or key not in checkpoint.get("awaiting_artifacts", [])
            ):
                raise Conflict("This task is not waiting for adoption of this candidate")
            # A shared artifact is only allowed to resume its explicit private parent Run.
            tool_id = self.session.scalar(
                select(AgentArtifact.tool_call_id).where(
                    AgentArtifact.id == values.artifact_id,
                    AgentArtifact.project_id == conversation.project_id,
                    AgentArtifact.episode_id == conversation.episode_id,
                )
            )
            tool = self.session.scalar(
                select(AgentToolCall)
                .where(AgentToolCall.id == tool_id, AgentToolCall.run_id == run.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if tool is None:
                raise NotFound("Candidate does not belong to this task")
            artifact = self.session.scalar(
                select(AgentArtifact)
                .where(AgentArtifact.id == values.artifact_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if artifact.status != "applied" or artifact.row_version != values.artifact_row_version:
                raise Conflict("Adopt this candidate before continuing")
            receipt = artifact.apply_receipt
            episode = self.session.scalar(
                select(Episode)
                .where(Episode.id == conversation.episode_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            for field in ("content_version", "storyboard_version", "episode_row_version"):
                actual = (
                    episode.row_version
                    if field == "episode_row_version"
                    else getattr(episode, field)
                )
                if field not in receipt or actual != int(receipt[field]):
                    raise Conflict("The work changed after adoption; review a new plan")
            if artifact.target_asset_id or artifact.target_shot_id:
                model = Asset if artifact.target_asset_id else ShotScript
                target = self.session.scalar(
                    select(model)
                    .where(model.id == (artifact.target_asset_id or artifact.target_shot_id))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if target is None or target.row_version != int(
                    receipt.get("target_row_version", 0)
                ):
                    raise Conflict("The adopted target changed; review a new plan")
            for step in checkpoint["authorization"]["steps"]:
                if step["id"] in checkpoint["authorization"].get("consumed_steps", []):
                    continue
                for field in ("content_version", "storyboard_version", "episode_row_version"):
                    step["source"][field] = int(receipt[field])
                if (
                    step["target_kind"] == "asset"
                    and step["target_id"] == str(artifact.target_asset_id)
                    or step["target_kind"] == "shot"
                    and step["target_id"] == str(artifact.target_shot_id)
                ):
                    step["source"]["target_row_version"] = int(receipt["target_row_version"])
                # Media retain reviewed hashes; an altered reference needs a new plan.
            checkpoint["continued_artifacts"] = {**previous, key: artifact.row_version}
            checkpoint.pop("awaiting_artifacts", None)
            checkpoint.pop("awaiting_step_id", None)
            # These arguments were generated against the pre-adoption work. Updating
            # the remaining approved source versions must not legitimize old creative
            # intents. Preserve their evidence and return a failure to the model so it
            # can read the adopted work and produce new intents in a fresh segment.
            turn_tools = self.session.scalars(
                select(AgentToolCall)
                .where(AgentToolCall.run_id == run.id, AgentToolCall.turn_id == tool.turn_id)
                .order_by(AgentToolCall.call_index)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            for pending in turn_tools:
                if pending.tool_name != "create_candidate" or pending.status not in {
                    "prepared",
                    "ready",
                }:
                    continue
                pending.status, pending.error = "failed", {"code": "stale_scope"}
                pending.result = {
                    "error": "stale_scope",
                    "instruction": (
                        "The work was adopted after these arguments were generated. "
                        "Read the current work before creating a fresh candidate for the "
                        "remaining approved step; do not reuse the old candidate arguments."
                    ),
                }
                pending.updated_at = utcnow()
            needs_tools = any(item.status in {"prepared", "ready"} for item in turn_tools)
            if not needs_tools:
                checkpoint["pending_results"] = {
                    "calls": {
                        item.provider_call_id: item.result
                        or {"error": (item.error or {}).get("code", "tool_failed")}
                        for item in turn_tools
                    }
                }
            run.checkpoint = checkpoint
            # Finish remaining non-creative calls through the normal coordinator;
            # otherwise all deferred results are ready for the next model segment.
            run.status = "queued"
            mark_scheduled(run, phase="tools" if needs_tools else "model")
            append_event(
                self.session,
                conversation,
                "run.continued",
                {"run_id": str(run.id), "artifact_id": str(artifact.id)},
                run_id=run.id,
            )
            self.session.flush()
            return read_run(self.session, run)
