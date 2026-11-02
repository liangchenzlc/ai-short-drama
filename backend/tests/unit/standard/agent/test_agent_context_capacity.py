"""Declared model capacity is tied to the immutable provider identity."""

import pytest

from short_drama.agent.context.budget import provider_budget
from short_drama.agent.context.capabilities import context_profile
from short_drama.agent.gateway.contracts import AgentGatewayError


@pytest.mark.parametrize(
    "error",
    [
        ValueError("unrelated"),
        TypeError("Model selected invalid tools: ['x']"),
        ValueError("Model selected malformed response"),
    ],
)
def test_selector_retry_predicate_rejects_unrelated_failures(error):
    from short_drama.agent.runtime.agent import invalid_tool_selection

    assert invalid_tool_selection(error) is False


def test_capacity_declaration_is_bound_to_identity_and_platform_ceiling():
    snapshot = {
        "row_version": 2,
        "model_key": "test",
        "base_url": "local",
        "credential_identity": "hash",
    }
    snapshot["capability_cache"] = {"agent_inputs": {**snapshot, "context_window_tokens": 1048576}}
    assert context_profile(snapshot) == {
        "context_window_tokens": 262144,
        "context_window_evidence": "declared",
    }
    snapshot["row_version"] = 3
    assert context_profile(snapshot)["context_window_tokens"] == 65536
    with pytest.raises(AgentGatewayError, match="agent_context_budget_exhausted"):
        provider_budget({"text": "a" * 90000}, 8192, context_window=16384)
