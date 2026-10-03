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
from short_drama.service.agent_conversation_service import AgentConversationService
from short_drama.service.agent_model_service import AgentModelService, model_snapshot
from short_drama.service.base import Page, utcnow
from short_drama.utils.snowflake import next_id

_UNSET = object()


def read_run(session, run, *, tool=_UNSET):
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
    )


class AgentRunService(AgentConversationService):
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
                    message=MessageRead.model_validate(existing),
                    run=read_run(self.session, run),
                    cursor=conversation.next_event_seq - 1,
                )
            if conversation.status != "active":
                raise WorkflowError("agent_conversation_archived", "请先恢复这段对话", 409)
            if self.session.scalar(
                select(AgentRun.id)
                .where(AgentRun.conversation_id == conversation.id, AgentRun.status.in_(ACTIVE))
                .limit(1)
                .with_for_update()
            ):
                raise WorkflowError("agent_run_active", "请先停止或完成当前任务", 409)
            actor = self._actor()
            model = AgentModelService(self.session, self.settings).select_model(
                values.model_config_id
            )
            task = values.task
            narrowed = discussion_only(values.content)
            authorization = {
                "mode": "discuss",
                "steps": [],
                "consumed_steps": [],
                "requires_plan": values.mode == "generate" and task is None and not narrowed,
                "user_constraints": values.content,
            }
            if values.mode == "generate" and task is not None and not narrowed:
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
            context = [{"role": item.role, "content": item.content} for item in reversed(recent)]
            now = utcnow()
            message = AgentMessage(
                id=next_id(),
                conversation_id=conversation.id,
                seq=conversation.next_message_seq,
                role="user",
                content=values.content,
                references=[],
                artifacts=[],
                idempotency_key=key,
                request_hash=request_hash,
                created_at=now,
            )
            conversation.next_message_seq += 1
            self.session.add(message)
            self.session.flush()
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
                    },
                    "user_prompt": values.content,
                    "conversation_context": context,
                },
                config_snapshot=model_snapshot(model),
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
                message=MessageRead.model_validate(message),
                run=read_run(self.session, run),
                cursor=conversation.next_event_seq - 1,
            )

    def list_messages(self, identifier, offset=0, limit=50):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
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
                items=[MessageRead.model_validate(row) for row in reversed(rows)],
                total=total,
                offset=offset,
                limit=limit,
            )

    def list_runs(self, identifier, offset=0, limit=20):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
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

    def events(self, identifier, cursor=0, *, verify_session=False):
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
            conversation = self._conversation(identifier)
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
