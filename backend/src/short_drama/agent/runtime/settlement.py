import hashlib
import json
from copy import deepcopy

from short_drama.agent.core.access import LeaseLost as LeaseLost
from short_drama.agent.core.access import lock_run as lock_run
from short_drama.agent.core.access import may_decide as may_decide
from short_drama.agent.core.access import model_unchanged as model_unchanged
from short_drama.agent.core.events import (
    TERMINAL,
    append_event,
    initial_usage,
)
from short_drama.agent.core.items import KIND_NARRATION, KIND_REPLY, allocate_item_seq
from short_drama.agent.core.state import (
    append_interrupted_message,
    finish_locked,
    mark_scheduled,
    run_artifact_references,
    safe_error,
)
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.runtime.base import RuntimeStoreBase
from short_drama.agent.runtime.claims import (
    _json_copy,
    active_seconds,
    budget_limit,
    checked_budget,
)
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentToolCall, AgentTurn
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id


class RuntimeSettlement(RuntimeStoreBase):
    def save_result(self, claim, normalized, partial_text=""):
        with self.factory.begin() as session:
            rows = lock_run(session, claim.run_id)
            if rows is None:
                raise LeaseLost()
            project, conversation, run = rows
            turn = session.get(AgentTurn, claim.turn_id)
            if conversation.status == "deleted" or turn is None:
                raise LeaseLost()
            if (claim.inputs or {}).get("kwargs", {}).get("harness_version"):
                from short_drama.agent.resources.contracts import assert_reference_payload

                assert_reference_payload(normalized)
            response = deepcopy(turn.response or {})
            if not response.get("raw"):
                raise AgentGatewayError("agent_response_not_durable", accepted_unknown=True)
            if response.get("applied") is True:
                return False
            normalized = response.get("normalized") or _json_copy(normalized)
            response["normalized"] = normalized
            response["applied"] = False
            turn.response, turn.status = response, "succeeded"
            turn.finished_at, turn.updated_at = utcnow(), utcnow()
            usage = {**initial_usage(), **(run.usage or {})}
            turn_usage = deepcopy(turn.usage or {})
            reported = normalized.get("usage") or {}
            input_tokens = reported.get("input_tokens")
            if (
                type(input_tokens) is int
                and input_tokens >= 0
                and not turn_usage.get("input_settled")
            ):
                usage["input_tokens"] = usage.get("input_tokens", 0) + input_tokens
                turn_usage["input_settled"] = True
                turn_usage["input_tokens"] = input_tokens
            tokens = reported.get("output_tokens")
            if (
                reported.get("output_tokens_reported") is True
                and type(tokens) is int
                and tokens >= 0
                and not turn_usage.get("settled")
            ):
                usage["reserved_output_tokens"] -= turn_usage.get("reserved_output_tokens", 0)
                usage["output_tokens"] += tokens
                turn_usage.update({"settled": True, "output_tokens": tokens})
            cached = reported.get("cached_tokens")
            if type(cached) is int and cached >= 0 and not turn_usage.get("cached_settled"):
                # Provider-reported prefix hits, summed once per settled turn.
                usage["cached_tokens"] = usage.get("cached_tokens", 0) + cached
                turn_usage.update({"cached_settled": True, "cached_tokens": cached})
            usage["uncached_input_tokens"] = max(
                0, usage.get("input_tokens", 0) - usage.get("cached_tokens", 0)
            )
            turn_usage["provider_usage"] = reported
            run.usage, turn.usage, run.updated_at = usage, turn_usage, utcnow()

            active = (
                run.status not in TERMINAL
                and run.message_version == claim.version
                and run.lease_token == claim.token
                and run.lease_until
                and run.lease_until > utcnow()
            )
            if not active:
                return False
            if run.cancel_requested:
                append_interrupted_message(session, conversation, run, partial_text)
                finish_locked(session, conversation, run, "cancelled")
                return False
            if not may_decide(session, project, conversation, run):
                finish_locked(session, conversation, run, "cancelled", {"code": "access_revoked"})
                return False
            if run.task_id is not None:
                from short_drama.agent.task.queries import task_for_run

                task = task_for_run(session, run)
                if claim.inputs.get(
                    "task_input_revision"
                ) != task.input_revision or claim.inputs.get("memory_epoch", 0) != (
                    run.checkpoint or {}
                ).get("memory_epoch", 0):
                    response.update(applied=True, superseded=True)
                    turn.response = response
                    mark_scheduled(run, phase="model")
                    return False
            budget = checked_budget(run)
            if usage["output_tokens"] + usage["reserved_output_tokens"] > budget_limit(
                budget, "output_tokens"
            ):
                finish_locked(
                    session, conversation, run, "failed", {"code": "agent_budget_exhausted"}
                )
                return False
            if normalized["output_kind"] == "tool_requests" and active_seconds(run) >= budget_limit(
                budget, "active_seconds"
            ):
                finish_locked(
                    session, conversation, run, "failed", {"code": "agent_budget_exhausted"}
                )
                return False
            checkpoint = deepcopy(run.checkpoint)
            if (run.checkpoint or {}).get("engine") == ENGINE_VERSION and run.task_id is not None:
                from short_drama.agent.harness.supervision import (
                    process_completion,
                    record_media_evidence,
                )
                from short_drama.agent.task.queries import task_for_run

                task = task_for_run(session, run)
                record_media_evidence(task, claim.inputs.get("kwargs", {}), turn.id)
                response["applied"] = True
                turn.response = response
                checked = process_completion(
                    session, conversation, task, run, turn, claim, normalized
                )
                if checked is None:
                    return False
                normalized = checked
                response = deepcopy(turn.response)
                checkpoint = deepcopy(run.checkpoint)
            checkpoint["history"] = normalized["history"]
            checkpoint["pending_results"] = {"calls": {}}
            run.checkpoint = checkpoint
            response["applied"] = True
            turn.response = response
            if normalized["output_kind"] == "tool_requests":
                narration = str(normalized.get("narration") or "").strip()[:4000]
                if narration:
                    item_seq = allocate_item_seq(conversation)
                    message = AgentMessage(
                        id=next_id(),
                        conversation_id=conversation.id,
                        task_id=run.task_id,
                        seq=conversation.next_message_seq,
                        item_seq=item_seq,
                        role="assistant",
                        kind=KIND_NARRATION,
                        content=narration,
                        references=[],
                        artifacts=[],
                        created_at=utcnow(),
                    )
                    conversation.next_message_seq += 1
                    session.add(message)
                    append_event(
                        session,
                        conversation,
                        "message.created",
                        {
                            "id": str(message.id),
                            "run_id": str(run.id),
                            "seq": message.seq,
                            "item_seq": item_seq,
                            "kind": KIND_NARRATION,
                            "role": "assistant",
                            "content": message.content,
                            "artifacts": [],
                        },
                        run_id=run.id,
                    )
            if normalized["output_kind"] == "text":
                authorization = checkpoint.get("authorization") or {}
                if authorization.get("mode") in {"single", "workflow"} and any(
                    step["id"] not in authorization.get("consumed_steps", [])
                    for step in authorization.get("steps", [])
                ):
                    finish_locked(
                        session,
                        conversation,
                        run,
                        "failed",
                        {
                            "code": "agent_plan_not_completed"
                            if authorization.get("mode") == "workflow"
                            else "agent_task_not_executed",
                        },
                    )
                    return False
                if checkpoint.get("discussion_revision"):
                    from short_drama.agent.task.discussion import replace_reply_locked

                    try:
                        replace_reply_locked(session, conversation, run, normalized["output"])
                    except AgentGatewayError as error:
                        finish_locked(session, conversation, run, "failed", {"code": error.code})
                        return False
                    finish_locked(session, conversation, run, "succeeded")
                    return False
                message = AgentMessage(
                    id=next_id(),
                    conversation_id=conversation.id,
                    task_id=run.task_id,
                    seq=conversation.next_message_seq,
                    item_seq=allocate_item_seq(conversation),
                    role="assistant",
                    kind=KIND_REPLY,
                    content=normalized["output"],
                    references=[
                        {
                            "type": "subject_scope",
                            "keys": checkpoint.get("subject_scope_keys", []),
                            "includes_attachments": True,
                        }
                    ]
                    if run.task_id is not None
                    else [],
                    artifacts=run_artifact_references(session, conversation, run),
                    created_at=utcnow(),
                )
                conversation.next_message_seq += 1
                session.add(message)
                append_event(
                    session,
                    conversation,
                    "message.created",
                    {
                        "id": str(message.id),
                        "run_id": str(run.id),
                        "seq": message.seq,
                        "item_seq": message.item_seq,
                        "kind": message.kind,
                        "role": "assistant",
                        "content": message.content,
                        "artifacts": message.artifacts,
                    },
                    run_id=run.id,
                )
                finish_locked(session, conversation, run, "succeeded")
                if run.task_id is not None:
                    from short_drama.agent.task.compaction import schedule_task_compaction_locked
                    from short_drama.agent.task.queries import task_for_run

                    # 回合后排压缩：把已结算输入整理进摘要，控制下一段的历史体积。
                    schedule_task_compaction_locked(
                        session, conversation, task_for_run(session, run), run
                    )
                return False
            if checkpoint.get("discussion_revision"):
                finish_locked(
                    session, conversation, run, "failed", {"code": "assistant_tools_forbidden"}
                )
                return False
            calls = normalized["output"].get("calls", [])
            if usage["tool_calls"] + len(calls) > budget_limit(budget, "tool_calls"):
                finish_locked(
                    session, conversation, run, "failed", {"code": "agent_tool_budget_exhausted"}
                )
                return False
            usage["tool_calls"] += len(calls)
            run.usage, run.phase = usage, "tools"
            for index, call in enumerate(calls, 1):
                raw_arguments = call["args"]
                try:
                    parsed = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else deepcopy(raw_arguments)
                    )
                    if not isinstance(parsed, dict):
                        parsed = None
                    else:
                        parsed = _json_copy(parsed)
                except (TypeError, ValueError):
                    parsed = None
                digest = hashlib.sha256(
                    json.dumps(
                        raw_arguments, ensure_ascii=False, sort_keys=True, allow_nan=False
                    ).encode()
                ).hexdigest()
                key = hashlib.sha256(
                    f"{run.id}:{turn.id}:{call['tool_call_id']}".encode()
                ).hexdigest()
                session.add(
                    AgentToolCall(
                        id=next_id(),
                        run_id=run.id,
                        turn_id=turn.id,
                        call_index=index,
                        provider_call_id=call["tool_call_id"],
                        tool_name=call["tool_name"],
                        arguments={"raw": raw_arguments, "parsed": parsed},
                        arguments_hash=digest,
                        idempotency_key=key,
                        status="prepared",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
            append_event(
                session,
                conversation,
                "decision.completed",
                {"run_id": str(run.id), "turn_id": str(turn.id), "tool_count": len(calls)},
                run_id=run.id,
            )
            return True

    def fail(self, claim, error):
        with self.factory.begin() as session:
            rows = lock_run(session, claim.run_id)
            if rows is None:
                return
            _project, conversation, run = rows
            if (
                run.status in TERMINAL
                or run.message_version != claim.version
                or run.lease_token != claim.token
            ):
                return
            turn = session.get(AgentTurn, claim.turn_id)
            if (
                error.code
                in {
                    "assistant_input_superseded",
                    "assistant_memory_superseded",
                    "assistant_subjects_superseded",
                    "assistant_attachments_superseded",
                    "assistant_policy_superseded",
                }
                and run.task_id is not None
            ):
                turn.status, turn.response = "succeeded", {"applied": True, "superseded": True}
                turn.updated_at = turn.finished_at = utcnow()
                mark_scheduled(run, phase="model")
                return
            if turn is not None and turn.status != "succeeded":
                turn.status = (
                    "unknown" if turn.status == "sent" and error.accepted_unknown else "failed"
                )
                turn.error, turn.finished_at, turn.updated_at = (
                    safe_error(error.code),
                    utcnow(),
                    utcnow(),
                )
            finish_locked(
                session,
                conversation,
                run,
                "failed",
                {
                    **safe_error(error.code),
                    "accepted_unknown": error.accepted_unknown,
                },
            )
