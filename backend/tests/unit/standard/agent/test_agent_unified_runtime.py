"""Unified intent handling stays bounded without paid capability probes."""

from types import SimpleNamespace

import pytest

from short_drama.agent.context.capabilities import input_capabilities
from short_drama.agent.core.state import failure_message
from short_drama.agent.gateway.protocol import provider_failure_code
from short_drama.schemas.assistant.operations.runtime import MessageCreate
from short_drama.service.ai_config.management.assistant_models import AgentModelService


def test_model_selection_does_not_require_paid_capability_evidence(monkeypatch):
    model = SimpleNamespace(id=7)
    service = AgentModelService(SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(service, "_actor", lambda: SimpleNamespace(user_id=1))
    monkeypatch.setattr(service, "available", lambda identifier, **_: model)
    assert service.select_model(7) is model


def test_unknown_model_family_is_not_a_multimodal_deny_list():
    support = input_capabilities(
        {"service_type": "text", "model_key": "custom-vl", "base_url": "https://local/v1"}
    )
    assert support["image"] is True
    assert support["audio"] is True
    assert support["evidence"] == "runtime"


def test_legacy_discussion_cannot_carry_a_task():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MessageCreate(
            content="仅讨论", mode="discuss", task={"kind": "novel", "instructions": "写小说"}
        )


@pytest.mark.parametrize(
    "status,detail,expected",
    [
        (400, "tools are not supported", "agent_tools_unsupported"),
        (400, "image input is not supported", "agent_image_input_unsupported"),
        (401, "private credential", "agent_provider_authentication"),
        (503, "tools are not supported", "agent_provider_unknown"),
    ],
)
def test_failure_classification_uses_actual_rejection_without_exposing_provider_details(
    status, detail, expected
):
    import json

    assert (
        provider_failure_code(status, json.dumps({"error": {"message": detail}}).encode())
        == expected
    )
    assert detail not in failure_message(expected)
    assert "重复生成" in failure_message(expected, accepted_unknown=True)
