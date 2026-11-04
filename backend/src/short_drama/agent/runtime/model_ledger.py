"""All real model admissions, including framework helpers, debit the parent Run."""

import asyncio
import logging
from copy import deepcopy

from short_drama.agent.context.budget import provider_budget
from short_drama.agent.context.capabilities import context_profile, request_output_tokens
from short_drama.agent.core import ENGINE_VERSION
from short_drama.agent.core.access import lock_run, may_decide, model_unchanged
from short_drama.agent.core.events import append_event, initial_usage
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.gateway.contracts import AgentGatewayError, ModelCallbacks
from short_drama.agent.runtime.claims import active_seconds, budget_limit, checked_budget
from short_drama.agent.task.queries import task_for_run
from short_drama.core.time import utcnow
from short_drama.dao.assistant.execution.model_ledger import AgentModelLedgerDAO
from short_drama.dao.assistant.execution.scope import AgentExecutionScopeDAO
from short_drama.domain.assistant.execution.models import AgentTurn
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)


class RunModelLedger:
    def __init__(self, context):
        self.context = context

    def callbacks(self, *, stream=True, max_output_tokens=None, **kwargs):
        async def admit(payload):
            try:
                await asyncio.to_thread(self.before_send, payload)
            except AgentGatewayError:
                raise
            except Exception:
                logger.exception(
                    "agent_model_admission_failed", extra={"run_id": self.context.run_id}
                )
                raise

        async def raw(payload):
            await asyncio.to_thread(self.save_raw, payload)

        async def usage(payload):
            await asyncio.to_thread(self.settle_usage, payload)

        async def error(payload):
            await asyncio.to_thread(self.save_error, payload)

        async def content(payload):
            if payload.get("channel") == "connection":
                await asyncio.to_thread(self.connection_state, payload)
                return
            if payload.get("channel") == "end" and payload.get("status") in {
                "succeeded",
                "interrupted",
            }:
                await asyncio.to_thread(self.complete_response, payload)
            if self.context.events is not None:
                from short_drama.agent.core.access import LeaseLost

                try:
                    await self.context.events.accept({"type": "model.content", **payload})
                except LeaseLost:
                    # Display admission can end before an accepted provider receipt settles.
                    return

        return ModelCallbacks(
            settings=self.context.settings,
            before_send=admit,
            save_raw=raw,
            usage=usage,
            error=error,
            on_model_content=content,
            stream=stream,
            max_output_tokens=max_output_tokens,
            timeout_seconds=getattr(self.context.settings, "agent_model_timeout_seconds", 120),
            **kwargs,
        )

    def connection_state(self, payload):
        with self.context.factory.begin() as session:
            conversation, run = self._bound(session, admission=True)
            if payload["status"] != "retrying":
                return
            checkpoint = deepcopy(run.checkpoint)
            connections = checkpoint.setdefault("model_connections", {})
            connections[payload["model_call_id"]] = {
                key: payload[key]
                for key in ("model_call_id", "retry_no", "max_retries", "timeout_seconds")
            }
            run.checkpoint, run.updated_at = checkpoint, utcnow()
            run.row_version += 1
            append_event(
                session,
                conversation,
                "model.connection",
                connections[payload["model_call_id"]],
                run_id=run.id,
            )

    def recovery_state(self, kind, model_call_id=None, *, expected_call_id=None):
        with self.context.factory.begin() as session:
            conversation, run = self._bound(session, admission=True)
            checkpoint = deepcopy(run.checkpoint)
            states = checkpoint.setdefault("recovery_states", {})
            key = str(self.context.scope_id)
            previous = states.get(key)
            if (
                expected_call_id is not None
                and (states.get(key) or {}).get("model_call_id") != expected_call_id
            ):
                return
            if kind is None:
                if key not in states:
                    return
                states.pop(key)
            else:
                value = {"scope_id": key, "kind": kind, "model_call_id": model_call_id}
                if previous == value:
                    return previous
                states[key] = value
            run.checkpoint, run.updated_at = checkpoint, utcnow()
            run.row_version += 1
            append_event(
                session,
                conversation,
                "run.recovery",
                {"recovery_states": list(states.values())},
                run_id=run.id,
            )
            return previous

    @staticmethod
    def clear_connection(run, call_id):
        checkpoint = deepcopy(run.checkpoint)
        checkpoint.get("model_connections", {}).pop(call_id, None)
        run.checkpoint = checkpoint

    def _bound(self, session, *, admission=False):
        context = self.context
        rows = lock_run(session, context.run_id)
        if rows is None:
            raise AgentGatewayError("agent_admission_denied")
        project, conversation, run = rows
        scope = AgentExecutionScopeDAO(
            session,
            owner_id=context.owner_id,
            run_id=run.id,
        ).get(scope_id=context.scope_id)
        if (
            conversation.owner_user_id != context.owner_id
            or run.task_id != context.task_id
            or scope is None
            or conversation.status == "deleted"
            or admission
            and (not context.fence[1] or not owns_execution(run, context.fence))
        ):
            raise AgentGatewayError("agent_admission_denied")
        if admission:
            task = task_for_run(session, run)
            if (
                not context.settings.agent_enabled
                or run.cancel_requested
                or not may_decide(session, project, conversation, run)
                or task.control_state != "none"
            ):
                raise AgentGatewayError("agent_admission_denied")
            if not model_unchanged(session, run):
                raise AgentGatewayError("agent_model_superseded")
        return conversation, run

    def before_send(self, request):
        with self.context.factory.begin() as session:
            conversation, run = self._bound(session, admission=True)
            self.clear_connection(run, request["model_call_id"])
            dao = AgentModelLedgerDAO(session, run.id)
            if dao.unresolved(self.context.scope_id, source=request["source"]) is not None:
                raise AgentGatewayError("agent_acceptance_unknown")
            budget = checked_budget(run)
            usage = {**initial_usage(), **run.usage}
            body = request["body"]
            amount = body.get(
                "max_completion_tokens", body.get("max_tokens", body.get("max_output_tokens"))
            )
            formal = run.checkpoint.get("engine") == ENGINE_VERSION
            if type(amount) is not int or amount <= 0:
                raise AgentGatewayError("invalid_agent_budget")
            maximum = (
                request_output_tokens(run.config_snapshot, request["source"], amount)
                if formal
                else budget["output_tokens_per_call"]
            )
            if amount > maximum:
                raise AgentGatewayError("invalid_agent_budget")
            if not formal and (
                usage["decision_calls"] >= budget_limit(budget, "decision_calls")
                or usage["output_tokens"] + usage["reserved_output_tokens"] + amount
                > budget_limit(budget, "output_tokens")
                or active_seconds(run) >= budget_limit(budget, "active_seconds")
            ):
                raise AgentGatewayError("agent_budget_exhausted")
            envelope = provider_budget(
                body,
                amount,
                references=True,
                context_window=context_profile(run.config_snapshot)["context_window_tokens"],
            )
            usage["decision_calls"] += 1
            usage["reserved_output_tokens"] += amount
            usage["last_context_budget"] = envelope
            run.usage, run.updated_at = usage, utcnow()
            now = utcnow()
            turn = AgentTurn(
                id=next_id(),
                run_id=run.id,
                scope_id=self.context.scope_id,
                turn_no=dao.next_number(self.context.scope_id),
                status="sent",
                request_messages=[deepcopy(request)],
                usage={
                    "model_call_id": request["model_call_id"],
                    "source": request["source"],
                    "reserved_output_tokens": amount,
                    "settled": False,
                },
                created_at=now,
                updated_at=now,
                started_at=now,
            )
            session.add(turn)
            append_event(
                session,
                conversation,
                "decision.started",
                {
                    "run_id": str(run.id),
                    "scope_id": str(self.context.scope_id),
                    "turn_id": str(turn.id),
                    "source": request["source"],
                },
                run_id=run.id,
            )

    def save_raw(self, payload):
        with self.context.factory.begin() as session:
            _conversation, run = self._bound(session)
            self.clear_connection(run, payload["model_call_id"])
            turn = AgentModelLedgerDAO(session, run.id).call(payload["model_call_id"])
            if turn is None:
                raise AgentGatewayError("agent_response_not_durable", accepted_unknown=True)
            response = deepcopy(turn.response or {})
            if response.get("raw") is not None and response["raw"] != payload:
                raise AgentGatewayError("agent_response_conflict", accepted_unknown=True)
            response["raw"] = deepcopy(payload)
            turn.response, turn.updated_at = response, utcnow()

    def settle_usage(self, payload):
        with self.context.factory.begin() as session:
            _conversation, run = self._bound(session)
            turn = AgentModelLedgerDAO(session, run.id).call(payload["model_call_id"])
            if turn is None or not (turn.response or {}).get("raw"):
                raise AgentGatewayError("agent_response_not_durable", accepted_unknown=True)
            if turn.usage.get("settled"):
                return
            usage, receipt = {**initial_usage(), **run.usage}, deepcopy(turn.usage)
            tokens = payload.get("output_tokens")
            if payload.get("output_tokens_reported") and type(tokens) is int and tokens >= 0:
                usage["reserved_output_tokens"] -= receipt["reserved_output_tokens"]
                usage["output_tokens"] += tokens
                receipt["settled"] = True
            for name in ("input_tokens", "cached_tokens"):
                value = payload.get(name)
                flag = "input_settled" if name == "input_tokens" else "cached_settled"
                if type(value) is int and value >= 0 and not receipt.get(flag):
                    usage[name] = usage.get(name, 0) + value
                    receipt[flag] = True
            usage["uncached_input_tokens"] = max(
                0, usage.get("input_tokens", 0) - usage.get("cached_tokens", 0)
            )
            receipt["provider_usage"] = deepcopy(payload)
            run.usage, turn.usage = usage, receipt
            if turn.error is None:
                turn.status = "succeeded"
            turn.updated_at, turn.finished_at = utcnow(), utcnow()

    def complete_response(self, payload):
        from short_drama.agent.resources.audit import record_deliveries_locked

        with self.context.factory.begin() as session:
            _conversation, run = self._bound(session)
            turn = AgentModelLedgerDAO(session, run.id).call(payload["model_call_id"])
            if turn is not None and turn.status == "succeeded" and turn.error is None:
                record_deliveries_locked(session, turn)

    def save_error(self, payload):
        with self.context.factory.begin() as session:
            _conversation, run = self._bound(session)
            self.clear_connection(run, payload["model_call_id"])
            turn = AgentModelLedgerDAO(session, run.id).call(payload["model_call_id"])
            if turn is None:
                return
            turn.status = "unknown" if payload["accepted_unknown"] else "failed"
            turn.error, turn.updated_at, turn.finished_at = deepcopy(payload), utcnow(), utcnow()
            if payload.get("not_accepted") and not turn.usage.get("settled"):
                run.usage = {
                    **run.usage,
                    "reserved_output_tokens": run.usage["reserved_output_tokens"]
                    - turn.usage["reserved_output_tokens"],
                }
                turn.usage = {**turn.usage, "settled": True}
