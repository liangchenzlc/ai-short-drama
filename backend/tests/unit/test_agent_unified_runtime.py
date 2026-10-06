"""Unified intent handling stays bounded without paid capability probes."""

from types import SimpleNamespace

import pytest

from short_drama.agent.input_capabilities import input_capabilities
from short_drama.agent.model_gateway import provider_failure_code
from short_drama.agent.state import failure_message
from short_drama.agent.tools import tool_manifest
from short_drama.schemas.agent_runtime import MessageCreate
from short_drama.service.agent_model_service import AgentModelService


def test_messages_default_to_auto_and_keep_explicit_discussion_read_only():
    assert MessageCreate(content="修改这个人物").mode == "auto"
    assert MessageCreate(content="仅讨论", mode="discuss").mode == "discuss"


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


def test_agent_has_prepare_and_status_tools_without_adoption_power():
    names = {tool.name for tool in tool_manifest()}
    assert {"prepare_task", "read_task_status", "create_candidate", "propose_plan"} <= names
    assert "adopt" not in names


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
