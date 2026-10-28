from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select

from short_drama.agent.core.access import LeaseLost as LeaseLost
from short_drama.agent.core.access import lock_run as lock_run
from short_drama.agent.core.access import may_decide as may_decide
from short_drama.agent.core.access import model_unchanged as model_unchanged
from short_drama.agent.core.events import (
    TERMINAL,
    append_event,
    initial_usage,
)
from short_drama.agent.core.state import (
    append_interrupted_message,
    finish_locked,
)
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.runtime.base import RuntimeStoreBase
from short_drama.agent.runtime.claims import (
    _json_copy,
    active_seconds,
    budget_limit,
    checked_budget,
)
from short_drama.domain.assistant.execution.models import AgentTurn
from short_drama.domain.identity.accounts.user import User
from short_drama.service.base import utcnow


class RuntimeSending(RuntimeStoreBase):
    def before_send(self, claim, request):
        if claim.inputs["kwargs"].get("harness_version"):
            from short_drama.agent.resources.contracts import assert_reference_payload

            assert_reference_payload(claim.inputs)
            assert_reference_payload(request)
        with self.factory.begin() as session:
            project, conversation, run = self._owned(
                session, claim.run_id, claim.version, claim.token
            )
            turn = session.get(AgentTurn, claim.turn_id)
            if run.task_id is not None:
                from short_drama.agent.task.queries import task_for_run

                task = task_for_run(session, run)
                if claim.inputs.get("task_input_revision") != task.input_revision:
                    raise AgentGatewayError("assistant_input_superseded")
                if claim.inputs.get("task_compaction_id") is not None:
                    from short_drama.agent.task.compaction import active_compaction_locked

                    current = active_compaction_locked(session, task)
                    if current is None:
                        raise AgentGatewayError("assistant_memory_superseded")
                from short_drama.service.assistant.tasks.policy import policy_state_locked

                session.scalar(
                    select(User.id).where(User.id == conversation.owner_user_id).with_for_update()
                )
                _enabled, policy_version = policy_state_locked(
                    session,
                    conversation.owner_user_id,
                    lock=True,
                )
                if claim.inputs.get("generation_policy_version") != policy_version:
                    raise AgentGatewayError("assistant_policy_superseded")
                if claim.inputs.get("memory_epoch", 0) != (run.checkpoint or {}).get(
                    "memory_epoch", 0
                ):
                    raise AgentGatewayError("assistant_memory_superseded")
                if claim.inputs.get("subject_access_keys") is not None:
                    from short_drama.service.assistant.context.subject import selection_metadata

                    available = sorted(
                        item["subject_key"]
                        for item in selection_metadata(
                            session, conversation.owner_user_id, task.context_snapshot
                        )
                        if item["availability"] != "unavailable"
                    )
                    if available != claim.inputs["subject_access_keys"]:
                        raise AgentGatewayError("assistant_subjects_superseded")
                if claim.inputs.get("result_access_keys") is not None:
                    from short_drama.agent.tools.canvas import canvas_result_keys
                    from short_drama.agent.tools.image import image_result_keys
                    from short_drama.agent.tools.standard import standard_result_keys

                    available = sorted(
                        standard_result_keys(session, conversation.owner_user_id, task)
                        | canvas_result_keys(session, conversation.owner_user_id, task)
                        | image_result_keys(session, conversation.owner_user_id, task)
                    )
                    if available != claim.inputs["result_access_keys"]:
                        raise AgentGatewayError("assistant_subjects_superseded")
                if claim.inputs.get("attachment_access_ids") is not None:
                    from short_drama.agent.context.attachments import (
                        available_attachment_ids,
                        task_attachments,
                    )
                    from short_drama.agent.memory.sources import restricted_messages

                    restricted_ids, _ = restricted_messages(session, conversation.id)
                    available = sorted(
                        available_attachment_ids(
                            session,
                            conversation,
                            task_attachments(session, task, restricted_ids=restricted_ids),
                        )
                    )
                    if available != claim.inputs["attachment_access_ids"]:
                        raise AgentGatewayError("assistant_attachments_superseded")
                if claim.inputs.get("image_content_access_keys") is not None:
                    from short_drama.agent.tools.image import available_image_contents_locked

                    available = sorted(
                        available_image_contents_locked(session, conversation, task, run)
                    )
                    if available != claim.inputs["image_content_access_keys"]:
                        raise AgentGatewayError("assistant_subjects_superseded")
            if (
                not self.settings.agent_enabled
                or run.cancel_requested
                or not may_decide(session, project, conversation, run)
                or turn.status != "prepared"
            ):
                raise AgentGatewayError("agent_admission_denied")
            # 模型配置在任务开始后被修改时，快照已失效；给出可操作的原因而不是笼统拒绝。
            if not model_unchanged(session, run):
                raise AgentGatewayError("agent_model_superseded")

            budget = checked_budget(run)
            usage = {**initial_usage(), **(run.usage or {})}
            amount = claim.inputs["kwargs"]["max_output_tokens"]
            context_budget = None
            if run.task_id is not None:
                from short_drama.agent.context.budget import provider_budget

                context_budget = provider_budget(
                    request.get("body", request),
                    amount,
                    references=bool(claim.inputs["kwargs"].get("harness_version")),
                    context_window=claim.inputs["kwargs"].get("context_window_tokens", 65536),
                )
            if (
                usage["decision_calls"] >= budget_limit(budget, "decision_calls")
                or usage["output_tokens"] + usage["reserved_output_tokens"] + amount
                > budget_limit(budget, "output_tokens")
                or active_seconds(run) >= budget_limit(budget, "active_seconds")
            ):
                raise AgentGatewayError("agent_budget_exhausted")
            usage["decision_calls"] += 1
            usage["reserved_output_tokens"] += amount
            if context_budget is not None:
                usage["last_context_budget"] = context_budget
            run.usage, run.updated_at = usage, utcnow()
            turn.request_messages = [deepcopy(claim.inputs), _json_copy(request)]
            turn.status, turn.started_at, turn.updated_at = "sent", utcnow(), utcnow()
            turn.usage = {"reserved_output_tokens": amount, "settled": False}
            if claim.inputs["kwargs"].get("harness_version"):
                turn.usage["context_metrics"] = deepcopy(
                    claim.inputs["kwargs"].get("context_frame", {}).get("context_metrics", {})
                )
            if context_budget is not None:
                turn.usage = {**turn.usage, "context_budget": context_budget}
            append_event(
                session,
                conversation,
                "decision.started",
                {"run_id": str(run.id), "turn_id": str(turn.id)},
                run_id=run.id,
            )

    def save_raw(self, claim, raw):
        # A late complete receipt is useful evidence even after stop/recovery.
        with self.factory.begin() as session:
            rows = lock_run(session, claim.run_id)
            if rows is None:
                raise LeaseLost()
            _project, _conversation, _run = rows
            if _conversation.status == "deleted":
                raise LeaseLost()
            turn = session.get(AgentTurn, claim.turn_id)
            if turn is None or turn.status == "prepared":
                raise LeaseLost()
            response = deepcopy(turn.response or {})
            if response.get("raw") is not None and response["raw"] != raw:
                raise AgentGatewayError("agent_response_conflict")
            response["raw"] = _json_copy(raw)
            turn.response, turn.updated_at = response, utcnow()

    def save_delta(self, claim, delta, offset):
        """Return ok / lost / cancelled so the stream loop can stop promptly."""
        if not delta:
            return "ok"
        with self.factory.begin() as session:
            try:
                _project, conversation, run = self._owned(
                    session, claim.run_id, claim.version, claim.token
                )
            except LeaseLost:
                return "lost"
            if run.cancel_requested:
                return "cancelled"
            if run.task_id is not None:
                from short_drama.agent.task.queries import task_for_run

                task = task_for_run(session, run)
                if task.input_revision != (run.checkpoint or {}).get("task_input_revision"):
                    return "stale"
                if claim.inputs.get("memory_epoch", 0) != (run.checkpoint or {}).get(
                    "memory_epoch", 0
                ):
                    return "stale"
            if not (run.checkpoint or {}).get("discussion_revision"):
                # A streamed text prefix can still become a rejected candidate.
                # Narration is published at settlement; replies after review.
                return "ok"
            append_event(
                session,
                conversation,
                "assistant.delta",
                {
                    "run_id": str(run.id),
                    "turn_id": str(claim.turn_id),
                    "delta": delta,
                    "offset": offset,
                },
                run_id=run.id,
            )
            return "ok"

    def save_interrupted(self, claim, text):
        """Persist the streamed prefix and close the run when the user stopped it."""
        with self.factory.begin() as session:
            try:
                _project, conversation, run = self._owned(
                    session, claim.run_id, claim.version, claim.token
                )
            except LeaseLost:
                return False
            if run.status in TERMINAL:
                return False
            append_interrupted_message(session, conversation, run, text)
            finish_locked(session, conversation, run, "cancelled")
            return True

    def heartbeat(self, claim):
        with self.factory.begin() as session:
            try:
                _project, _conversation, run = self._owned(
                    session, claim.run_id, claim.version, claim.token
                )
            except LeaseLost:
                return False
            if not self.settings.agent_enabled or run.cancel_requested:
                return False
            run.lease_until = utcnow() + timedelta(seconds=self.settings.agent_lease_seconds)
            return True
