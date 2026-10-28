"""LangGraph decisions and durable tool boundaries on the existing worker lease.

Each broker delivery advances the graph to the next durable scheduling boundary.
The next delivery reconstructs references from MySQL; SDKs never own a tool loop.
"""

import asyncio
import contextlib
import logging
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from short_drama.agent.context.assembly import prepare_decision
from short_drama.agent.context.transcript import (
    model_messages,
    restore_transcript,
    tool_result_references,
)
from short_drama.agent.core.access import LeaseLost
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.gateway.langchain import LangChainGateway, select_protocol
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.harness.checkpointer import MySQLCheckpointSaver
from short_drama.agent.resources.contracts import HydrationIntent, assert_reference_payload
from short_drama.agent.resources.resolver import ResourceResolver
from short_drama.agent.runtime.claims import Claim, thaw_input
from short_drama.agent.runtime.decisions import execute_tools
from short_drama.agent.runtime.delta_buffer import _DeltaBuffer
from short_drama.agent.runtime.executor import AgentRuntimeStore
from short_drama.agent.task.queries import task_for_run
from short_drama.core.exceptions import BusinessError
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from short_drama.service.ai_config.management.assistant_models import model_credentials
from short_drama.service.assistant.execution.graph import AgentGraphService
from short_drama.service.media.files.storage import StorageService
from short_drama.storage.minio import MinioStorage

logger = logging.getLogger(__name__)


class HarnessState(TypedDict):
    run_id: int
    task_id: int
    turn_id: int | None
    phase: Literal["model", "tools", "done", "failed", "lost"]


class LeaseWatch:
    """Renew one delivery's execution lease across both graph nodes.

    A failed renewal only marks the lease suspect and stops renewing. It never
    cancels the in-flight provider request: an already-sent request must be
    allowed to produce its receipt, which ``save_raw`` persists without a lease.
    """

    def __init__(self, store, claim, settings):
        self.store = store
        self.claim = claim
        self.interval = max(5, int(settings.agent_lease_seconds) // 3)
        self.suspect = False
        self._stopped = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(self._watch())

    async def stop(self):
        self._stopped.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _watch(self):
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval)
                return
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                return
            alive = await asyncio.to_thread(self.store.heartbeat, self.claim)
            if not alive:
                self.suspect = True
                logger.warning("agent_lease_suspect", extra={"run_id": self.claim.run_id})
                return


@dataclass(frozen=True, repr=False)
class ExecutionContext:
    claim: Claim
    watch: LeaseWatch


class HarnessRuntime:
    def __init__(self, factory, settings, gateway=None, *, resolver_factory=None):
        self.factory, self.settings = factory, settings
        self.gateway = gateway or LangChainGateway(settings)
        self.store = AgentRuntimeStore(factory, settings)
        self.resolver_factory = resolver_factory

    def execute_one(self, run_id, message_version):
        claim = self.store.claim_execution(
            int(run_id),
            int(message_version),
            partial(prepare_decision, settings=self.settings),
        )
        if claim is None:
            return
        with self.factory.begin() as session:
            run = session.get(AgentRun, claim.run_id)
            owner = run.initiated_by
            if (run.checkpoint or {}).get("engine") != ENGINE_VERSION:
                raise AgentGatewayError("invalid_agent_engine")
        service = AgentGraphService(
            self.factory,
            owner=owner,
            task=claim.task_id,
            run=claim.run_id,
            namespace=f"run:{claim.run_id}:delivery:{claim.version}",
            version=ENGINE_VERSION,
        )
        saver = MySQLCheckpointSaver(service)
        try:
            asyncio.run(self._deliver(claim, saver))
        except LeaseLost:
            return
        except AgentGatewayError as error:
            self.store.fail(claim, error)
        except Exception:
            logger.exception("agent_harness_action_interrupted", extra={"run_id": claim.run_id})
            raise RuntimeError("Harness action interrupted; database recovery required") from None

    async def _deliver(self, claim, saver):
        """One broker delivery: one model decision, then its tool batch."""
        watch = LeaseWatch(self.store, claim, self.settings)
        graph = self._graph(saver)
        state = {
            "run_id": claim.run_id,
            "task_id": claim.task_id,
            "turn_id": claim.turn_id,
            "phase": claim.phase,
        }
        watch.start()
        try:
            await graph.ainvoke(
                state,
                config={**saver._config(), "recursion_limit": 8},
                context=ExecutionContext(claim=claim, watch=watch),
                durability="sync",
            )
        finally:
            await watch.stop()

    def _graph(self, saver):
        builder = StateGraph(HarnessState, context_schema=ExecutionContext)
        builder.add_node("model", self._model_node)
        builder.add_node("tools", self._tools_node)
        builder.add_conditional_edges(
            START, lambda state: state["phase"], {"model": "model", "tools": "tools"}
        )
        builder.add_conditional_edges(
            "model",
            lambda state: state["phase"],
            {"tools": "tools", "done": END, "failed": END, "lost": END},
        )
        builder.add_edge("tools", END)
        return builder.compile(checkpointer=saver, name=ENGINE_VERSION)

    async def _model_node(self, state: HarnessState, runtime: Runtime[ExecutionContext]):
        claim = runtime.context.claim
        try:
            more = await self._decision(claim)
            return {"phase": "tools" if more else "done"}
        except LeaseLost:
            return {"phase": "lost"}
        except (AgentGatewayError, BusinessError) as error:
            safe = error if isinstance(error, AgentGatewayError) else AgentGatewayError(error.code)
            await asyncio.to_thread(self.store.fail, claim, safe)
            return {"phase": "failed"}

    async def _tools_node(self, state: HarnessState, runtime: Runtime[ExecutionContext]):
        claim = runtime.context.claim
        await asyncio.to_thread(
            execute_tools,
            self.factory,
            self.settings,
            claim.run_id,
            fence=(claim.version, claim.token),
            watch=runtime.context.watch,
        )
        return {"phase": "done"}

    def _freeze_images(self, claim, values, resolver, protocol, groups):
        intents, hydrated, outcomes = [], [], []
        operations = {
            item["tool_call_id"]: item["operation_id"]
            for group in groups
            for item in group["results"]
        }
        source_turns = {
            item["tool_call_id"]: group["turn_id"] for group in groups for item in group["results"]
        }
        frozen_before = values.get("resource_outcomes") is not None
        total_bytes = 0
        for value in values.get("hydration_intents", []):
            intent = HydrationIntent.model_validate(value)
            intent = intent.model_copy(update={"invocation_id": f"turn:{claim.turn_id}"})
            outcome = {
                "tool_call_id": intent.tool_call_id,
                "operation_id": operations.get(intent.tool_call_id),
                "source_turn_id": source_turns.get(intent.tool_call_id),
                "resource": intent.resource.model_dump(mode="json"),
            }
            try:
                views = resolver.freeze(intent)
                resources = [resolver.hydrate(view, protocol=protocol) for view in views]
                size = sum(item.reference.metadata.get("byte_size", 0) for item in resources)
                if total_bytes + size > 4 * 1024**2:
                    from short_drama.core.exceptions import WorkflowError

                    raise WorkflowError(
                        "agent_resource_too_large",
                        "本次媒体总量超过 4 MiB，请减少图片或降低 detail",
                        422,
                    )
                total_bytes += size
                intents.extend(views)
                hydrated.extend(resources)
                outcomes.append({**outcome, "status": "ready"})
            except BusinessError as error:
                # A sent request is immutable: missing replay material must not
                # change it into a different, live request.
                if claim.request is not None or frozen_before:
                    raise
                outcomes.append(
                    {
                        **outcome,
                        "status": "failed",
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "details": error.details
                            if error.code == "agent_resource_crop"
                            else None,
                            "retryable": error.code
                            in {
                                "agent_resource_crop",
                                "agent_resource_too_large",
                                "agent_resource_unavailable",
                            },
                        },
                    }
                )
        if frozen_before:
            outcomes = values["resource_outcomes"]
        frozen = [item.model_dump(mode="json") for item in intents]
        if not frozen_before and claim.request is None:
            envelope = deepcopy(claim.inputs)
            envelope["kwargs"]["hydration_intents"] = frozen
            envelope["kwargs"]["resource_outcomes"] = outcomes
            assert_reference_payload(envelope)
            with self.factory.begin() as session:
                self.store._owned(session, claim.run_id, claim.version, claim.token)
                turn = session.get(AgentTurn, claim.turn_id)
                if turn.status != "prepared":
                    raise AgentGatewayError("agent_request_already_sent", accepted_unknown=True)
                turn.request_messages = [envelope]
            claim.inputs.clear()
            claim.inputs.update(envelope)
            values["hydration_intents"] = frozen
            values["resource_outcomes"] = outcomes
        return intents, hydrated

    async def _decision(self, claim):
        values = thaw_input(claim.inputs)
        # A completion review is a different subject: its own frozen model.
        snapshot = values.get("reviewer_snapshot") or claim.snapshot
        protocol = select_protocol(snapshot)
        storage = None
        try:
            if self.resolver_factory:
                resolver = self.resolver_factory()
            elif values.get("hydration_intents"):
                storage = MinioStorage(self.settings)
                resolver = ResourceResolver(StorageService(storage, self.settings), self.settings)
            else:
                resolver = ResourceResolver(None)
            with self.factory.begin() as session:
                run = session.get(AgentRun, claim.run_id)
                task = task_for_run(session, run)
                groups = restore_transcript(session, run, task, values["history_manifest"])
            intents, hydrated = await asyncio.to_thread(
                self._freeze_images, claim, values, resolver, protocol, groups
            )
            deliveries = {}
            for intent, item in zip(intents, hydrated, strict=True):
                deliveries.setdefault(intent.tool_call_id, []).append(item)
            messages = model_messages(
                values["instructions"],
                values["context_frame"],
                groups,
                deliveries,
                intents,
                protocol,
                continuation_prompt=values.get("continuation_prompt"),
                resource_outcomes=values.get("resource_outcomes", []),
            )
            delta = _DeltaBuffer(self.store, claim)

            async def on_request(payload):
                await asyncio.to_thread(self.store.before_send, claim, payload)

            async def on_response(payload):
                await asyncio.to_thread(self.store.save_raw, claim, payload)

            async def on_text_delta(text):
                await asyncio.to_thread(delta.add, text)

            try:
                normalized = await self.gateway.decide(
                    snapshot,
                    model_credentials(snapshot, self.settings.encryption_key),
                    messages=messages,
                    tools=values["tools"],
                    resources=hydrated,
                    tool_results=tool_result_references(groups),
                    max_output_tokens=values["max_output_tokens"],
                    max_tool_calls=values["max_tool_calls"],
                    timeout_seconds=values["timeout_seconds"],
                    stream=values["stream"],
                    on_request=on_request,
                    on_response=on_response,
                    on_text_delta=on_text_delta,
                    request=claim.request,
                    raw=claim.raw,
                )
            except asyncio.CancelledError:
                raise LeaseLost() from None
            await asyncio.to_thread(delta.add, "", final=True)
            normalized["history"] = {
                "codec": "agent.harness-history",
                "version": 1,
                "through_turn_id": str(claim.turn_id),
            }
            return await asyncio.to_thread(self.store.save_result, claim, normalized, delta.text)
        finally:
            if storage is not None:
                storage.close()
