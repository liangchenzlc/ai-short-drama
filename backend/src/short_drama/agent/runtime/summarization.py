"""Bounded helper input for the official summarization middleware's public callback."""

import asyncio
import json
from uuid import uuid4

from langchain.agents.middleware.summarization import DEFAULT_SUMMARY_PROMPT
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import convert_to_openai_messages, get_buffer_string

from short_drama.agent.context.budget import (
    SAFETY_TOKENS,
    estimate_input_tokens,
    input_limit,
    wire_tool_counts,
)
from short_drama.agent.context.capabilities import context_profile, request_output_tokens
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.runtime.model_ledger import RunModelLedger


class BoundedSummary:
    def __init__(self, model, *, context=None):
        self.model = model
        self.calls = 0
        self.context = context

    async def summarize(self, messages, config):
        if self.context is None:
            return await self._summarize(messages, config)
        ledger, call_id = RunModelLedger(self.context), f"summary:{uuid4().hex}"
        previous = await asyncio.to_thread(ledger.recovery_state, "context_compaction", call_id)
        try:
            return await self._summarize(messages, config)
        finally:
            await asyncio.to_thread(
                ledger.recovery_state,
                previous["kind"] if previous else None,
                previous["model_call_id"] if previous else None,
                expected_call_id=call_id,
            )

    async def _summarize(self, messages, config):
        source = get_buffer_string(messages, format="xml")
        snapshot = getattr(self.model, "snapshot", {})
        callbacks = getattr(self.model, "model_callbacks", None)
        output = request_output_tokens(
            snapshot, "summarization", getattr(callbacks, "max_output_tokens", None)
        )
        window = context_profile(snapshot)["context_window_tokens"]
        limit = input_limit(output, context_window=window)
        project = getattr(callbacks, "project_input", None) or convert_to_openai_messages
        summary, offset = "", 0

        def prompt(fragment):
            value = json.dumps(
                {
                    "previous_summary": summary,
                    "offset": offset,
                    "total_chars": len(source),
                    "history_fragment": fragment,
                },
                ensure_ascii=False,
            )
            return (
                DEFAULT_SUMMARY_PROMPT.replace(
                    "<messages>",
                    "<progressive_summary>Merge the previous summary with this consecutive history "
                    "fragment. A fragment is not the complete history. Preserve the goal, latest "
                    "corrections, source/Skill identities, resource paths and exact read cursors, "
                    "saved business IDs and unfinished dependencies. Do not claim omitted content "
                    "was read.</progressive_summary>\n<messages>",
                )
                .format(messages=value)
                .rstrip()
            )

        def count(fragment):
            return estimate_input_tokens(
                {
                    "model": snapshot.get("model_key", ""),
                    "messages": project([HumanMessage(content=prompt(fragment))]),
                    "max_tokens": output,
                },
                references=True,
            )

        # 另留一份平台安全余量吸收三种SDK的格式字段；最终仍由真实body的guard校验。
        helper_limit = limit - SAFETY_TOKENS
        while offset < len(source):
            left, right = offset, len(source)
            while left < right:
                middle = (left + right + 1) // 2
                if count(source[offset:middle]) <= helper_limit:
                    left = middle
                else:
                    right = middle - 1
            if left == offset:
                raise AgentGatewayError(
                    "agent_context_budget_exhausted",
                    context_budget={
                        "phase": "helper",
                        "source": "summarization",
                        "context_window_tokens": window,
                        "reserved_output_tokens": output,
                        "input_limit_tokens": limit,
                        "helper_input_limit_tokens": helper_limit,
                        "estimated_input_tokens": count(source[offset : offset + 1]),
                        "fixed_estimated_input_tokens": count(""),
                        "fixed_kind": "helper_prompt_and_prior_summary",
                        "message_count": len(messages),
                        **wire_tool_counts(convert_to_openai_messages(messages)),
                        "covered_characters": offset,
                        "total_characters": len(source),
                    },
                )
            response = await self.model.ainvoke(
                [HumanMessage(content=prompt(source[offset:left]))], config=config
            )
            self.calls += 1
            next_summary = response.text.strip()
            if not next_summary:
                raise AgentGatewayError("agent_summary_empty", requests=self.calls)
            summary, offset = next_summary, left
        return summary
