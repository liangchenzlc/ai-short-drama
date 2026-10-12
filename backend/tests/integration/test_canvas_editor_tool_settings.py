"""内置编辑器工具的本人开关、持久恢复与模型偏好保护。"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_identity_collaboration import account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
STATUS = "/api/v1/canvas-runtime/plugins/status"
ACTIVATION = "/api/v1/canvas-runtime/plugins/{}/activation"
MODELS = "/api/v1/canvas-runtime/workspace/model-config"


def test_tool_activation_is_private_durable_and_preserves_model_preferences(identity_app):
    owner, _user = account(identity_app, "tool_settings_owner")
    other, _other = account(identity_app, "tool_settings_other")
    initial = owner.get(STATUS)
    assert initial.status_code == 200, initial.text
    initial = initial.json()
    assert set(initial["states"]) == {
        "prompt-optimizer",
        "ai-art-critique",
        "runninghub-workflow-provider",
    }
    for state in initial["states"].values():
        assert state["platformAvailable"] and state["canToggle"]
        assert not state["effectiveEnabled"] and not state["userConfigured"]
    saved = owner.put(MODELS, json={"expected_row_version": "0", "preferences": {"size": "2K"}})
    assert saved.status_code == 200, saved.text
    enabled = owner.put(ACTIVATION.format("prompt-optimizer"), json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"]["effectiveEnabled"]
    assert enabled.json()["state"]["userConfigured"]
    assert not other.get(STATUS).json()["states"]["prompt-optimizer"]["effectiveEnabled"]
    models = owner.get(MODELS).json()
    assert models["preferences"] == {"size": "2K"}
    assert int(models["row_version"]) > int(saved.json()["row_version"])
    stale = owner.put(
        MODELS, json={"expected_row_version": saved.json()["row_version"], "preferences": {}}
    )
    assert stale.status_code == 409
    updated = owner.put(
        MODELS, json={"expected_row_version": models["row_version"], "preferences": {"size": "4K"}}
    )
    assert updated.status_code == 200, updated.text
    fresh = TestClient(identity_app[0])
    fresh.cookies.update(owner.cookies)
    assert fresh.get(STATUS).json()["states"]["prompt-optimizer"]["effectiveEnabled"]
    assert owner.get(MODELS).json()["preferences"] == {"size": "4K"}
    disabled = owner.put(ACTIVATION.format("prompt-optimizer"), json={"enabled": False})
    assert disabled.status_code == 200
    assert (
        disabled.json()["state"]["userConfigured"]
        and not disabled.json()["state"]["effectiveEnabled"]
    )
    assert owner.get(MODELS).json()["preferences"] == {"size": "4K"}


@pytest.mark.parametrize("enabled", ["true", 1, None, {}])
def test_tool_activation_rejects_non_boolean_or_client_scope(identity_app, enabled):
    client, _user = account(identity_app, "tool_settings_invalid")
    refused = client.put(ACTIVATION.format("prompt-optimizer"), json={"enabled": enabled})
    assert refused.status_code == 422, refused.text
    refused = client.put(
        ACTIVATION.format("prompt-optimizer"), json={"enabled": True, "userId": "1"}
    )
    assert refused.status_code == 422
    unknown = client.put(ACTIVATION.format("uploaded-tool"), json={"enabled": True})
    assert unknown.status_code == 404
    assert not client.get(STATUS).json()["states"]["prompt-optimizer"]["effectiveEnabled"]


def test_tool_activation_requires_authenticated_actor_and_csrf(identity_app):
    anonymous = TestClient(identity_app[0])
    assert anonymous.get(STATUS).status_code == 401
    client, user = account(identity_app, "tool_settings_auth")
    refused = client.put(
        ACTIVATION.format("prompt-optimizer"),
        json={"enabled": True},
        headers={"X-CSRF-Token": "wrong"},
    )
    assert refused.status_code == 403
    changed = client.get(STATUS, headers={"X-Canvas-Actor": str(int(user["id"]) + 1)})
    assert changed.status_code == 409
