from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from short_drama.agent.core.access import LeaseLost as LeaseLost
from short_drama.agent.core.access import lock_run as lock_run
from short_drama.agent.core.access import may_decide as may_decide
from short_drama.agent.core.access import model_unchanged as model_unchanged
from short_drama.agent.core.events import (
    TERMINAL,
    append_event,
)
from short_drama.agent.core.state import (
    finish_locked,
    mark_scheduled,
)
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.runtime.base import RuntimeStoreBase
from short_drama.agent.runtime.claims import (
    Claim,
    _cancelled_code,
    active_seconds,
    budget_limit,
    checked_budget,
    freeze_input,
    latest_turn,
    preceding_execution,
)
from short_drama.domain.assistant.execution.models import AgentTurn
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id


class RuntimeClaiming(RuntimeStoreBase):
    def claim_execution(self, run_id, version, prepare_decision):
        if not self.settings.agent_enabled:
            return None
        now = utcnow()
        with self.factory.begin() as session:
            rows = lock_run(session, run_id)
            if rows is None:
                return None
            project, conversation, run = rows
            from short_drama.dao.assistant.tasks.task import AgentTaskDAO

            active_operation = (
                AgentTaskDAO(session).active_operation(run.task_id)
                if run.task_id is not None
                else None
            )
            if (
                run.status not in {"queued", "running"}
                or run.message_version != version
                or run.message_status not in {"pending", "publishing", "published"}
                or (run.lease_token is not None and run.message_status != "publishing")
                or run.next_run_at is None
                or run.next_run_at > now
            ):
                return None

            if active_operation is None and run.cancel_requested:
                finish_locked(
                    session, conversation, run, "cancelled", {"code": _cancelled_code(session, run)}
                )
                return None
            if active_operation is None and not may_decide(session, project, conversation, run):
                finish_locked(session, conversation, run, "cancelled", {"code": "access_revoked"})
                return None
            if preceding_execution(session, run) is not None:
                run.message_status = "pending"
                run.lease_token = run.lease_until = None
                return None
            if run.task_id is not None:
                from short_drama.agent.task.controls import apply_controls_locked
                from short_drama.agent.task.queries import task_for_run
                from short_drama.agent.task.state import apply_inputs_locked

                task = task_for_run(session, run)
                apply_controls_locked(session, conversation, task, run)
                if run.cancel_requested:
                    # A stop applied at this boundary must not buy another model call.
                    finish_locked(
                        session,
                        conversation,
                        run,
                        "cancelled",
                        {"code": _cancelled_code(session, run)},
                    )
                    return None
                if run.status in TERMINAL or run.message_version != version:
                    return None
                apply_inputs_locked(session, conversation, task, run)
                task.status = "running"
                task.phase = active_operation.phase if active_operation else "deciding"
                task.row_version += 1
                task.updated_at = now

            if run.status == "queued":
                append_event(
                    session, conversation, "run.started", {"run_id": str(run.id)}, run_id=run.id
                )
            run.status = "running"
            run.started_at = run.started_at or now
            run.updated_at = now
            run.message_status = "idle"
            run.lease_token, run.lease_until = (
                uuid4().hex,
                now + timedelta(seconds=self.settings.agent_lease_seconds),
            )
            checkpoint = deepcopy(run.checkpoint)
            checkpoint.setdefault("runtime", {})["active_since"] = now.isoformat()
            # 清零记忆等待退避计数器
            checkpoint.pop("memory_wait_count", None)
            run.checkpoint = checkpoint
            turn = latest_turn(session, run.id)
            if run.phase == "tools":
                try:
                    if active_operation is None and active_seconds(run) >= budget_limit(
                        checked_budget(run), "active_seconds"
                    ):
                        raise AgentGatewayError("agent_budget_exhausted")
                except AgentGatewayError as error:
                    finish_locked(session, conversation, run, "failed", {"code": error.code})
                    return None
                return Claim(
                    run.id,
                    version,
                    run.lease_token,
                    "tools",
                    None,
                    {},
                    None,
                    None,
                    None,
                    task_id=run.task_id,
                )
            if turn is None or (
                turn.status == "succeeded" and (turn.response or {}).get("applied") is True
            ):
                try:
                    inputs = freeze_input(prepare_decision(session, conversation, run), run)
                except AgentGatewayError as error:
                    waiting = error.code == "agent_memory_waiting"
                    if error.code == "agent_context_budget_exhausted" and run.task_id is not None:
                        from short_drama.agent.task.compaction import task_compaction_waiting_locked

                        waiting = task_compaction_waiting_locked(session, conversation, run)
                    if waiting and run.task_id is not None:
                        wait_count = (run.checkpoint or {}).get("memory_wait_count", 0) + 1
                        delay = min(60, 3 * (2 ** (wait_count - 1)))
                        checkpoint = deepcopy(run.checkpoint or {})
                        checkpoint["memory_wait_count"] = wait_count
                        run.checkpoint = checkpoint
                        mark_scheduled(run, phase="model", when=now + timedelta(seconds=delay))
                        return None
                    finish_locked(session, conversation, run, "failed", {"code": error.code})
                    return None
                number = (turn.turn_no if turn else 0) + 1
                turn = AgentTurn(
                    id=next_id(),
                    run_id=run.id,
                    turn_no=number,
                    request_messages=[inputs],
                    status="prepared",
                    usage={},
                    created_at=now,
                    updated_at=now,
                )
                session.add(turn)
                session.flush()
            raw = (turn.response or {}).get("raw")
            if turn.status != "prepared" and raw is None:
                turn.status, turn.error, turn.updated_at = (
                    "unknown",
                    {"code": "agent_acceptance_unknown"},
                    now,
                )
                turn.finished_at = now
                finish_locked(session, conversation, run, "failed", turn.error)
                return None
            return Claim(
                run.id,
                version,
                run.lease_token,
                "model",
                turn.id,
                deepcopy(run.config_snapshot),
                deepcopy(turn.request_messages[0]),
                deepcopy(turn.request_messages[1]) if len(turn.request_messages) > 1 else None,
                deepcopy(raw),
                task_id=run.task_id,
            )
