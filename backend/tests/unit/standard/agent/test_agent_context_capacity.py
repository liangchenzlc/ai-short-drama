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


def test_capacity_declaration_is_bound_to_identity_without_platform_ceiling():
    snapshot = {
        "row_version": 2,
        "model_key": "test",
        "base_url": "local",
        "credential_identity": "hash",
    }
    snapshot["capability_cache"] = {
        "agent_inputs": {**snapshot, "context_window_tokens": 1048576, "max_output_tokens": 32768}
    }
    assert context_profile(snapshot) == {
        "context_window_tokens": 1048576,
        "context_window_evidence": "declared",
        "max_output_tokens": 32768,
        "output_capacity_evidence": "declared",
    }
    snapshot["row_version"] = 3
    assert context_profile(snapshot)["context_window_tokens"] == 65536
    assert context_profile(snapshot)["max_output_tokens"] == 8192
    with pytest.raises(AgentGatewayError, match="agent_context_budget_exhausted"):
        provider_budget({"text": "a" * 90000}, 8192, context_window=16384)


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_official_deepseek_profile_uses_native_capacity(model):
    assert context_profile({"base_url": "https://api.deepseek.com/v1", "model_key": model}) == {
        "context_window_tokens": 1_000_000,
        "context_window_evidence": "official_profile",
        "max_output_tokens": 393_216,
        "output_capacity_evidence": "official_profile",
    }


@pytest.mark.parametrize(
    "base_url, model",
    [
        ("https://api.deepseek.com.example.test/v1", "deepseek-flash"),
        ("https://proxy.example.test/v1", "deepseek-v4-pro"),
        ("https://api.deepseek.com/v1", "deepseek-chat"),
    ],
)
def test_unknown_endpoints_and_models_keep_conservative_defaults(base_url, model):
    profile = context_profile({"base_url": base_url, "model_key": model})
    assert profile["context_window_tokens"] == 65536
    assert profile["max_output_tokens"] == 8192
    assert profile["output_capacity_evidence"] == "platform_default"


def test_explicit_capacity_overrides_official_profile():
    snapshot = {
        "row_version": 1,
        "model_key": "deepseek-flash",
        "base_url": "https://api.deepseek.com/v1",
        "credential_identity": "hash",
    }
    snapshot["capability_cache"] = {
        "agent_inputs": {**snapshot, "context_window_tokens": 524288, "max_output_tokens": 65536}
    }
    assert context_profile(snapshot)["context_window_tokens"] == 524288
    assert context_profile(snapshot)["max_output_tokens"] == 65536


def test_request_output_tokens_respects_auxiliary_roles_and_declared_capacity():
    from short_drama.agent.context.capabilities import request_output_tokens

    official = {"base_url": "https://api.deepseek.com", "model_key": "deepseek-flash"}
    assert request_output_tokens(official, "main") == 393216
    assert request_output_tokens(official, "main", requested=500000) == 393216
    assert request_output_tokens(official, "main", requested=256) == 256
    assert request_output_tokens(official, "tool_selection") == 2048
    assert request_output_tokens(official, "summarization") == 8192
    assert request_output_tokens(official, "conversation_title") == 64
    assert request_output_tokens(official, "tool_selection", requested=4096) == 2048


def test_unknown_model_output_can_grow_within_context_window():
    from short_drama.agent.context.capabilities import request_output_tokens

    snapshot = {"base_url": "https://proxy.example.test", "model_key": "custom"}
    assert request_output_tokens(snapshot, "main") == 8192
    assert request_output_tokens(snapshot, "main", requested=16384) == 16384
    assert request_output_tokens(snapshot, "main", requested=65536) == 58368
