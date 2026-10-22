"""时间线只读编译必须认证；未绑定数据库的 Session 证明不会排队或读取媒体。"""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.core.identity import ActorContext
from short_drama.main import create_app

PATH = "/api/v1/canvas-runtime/timeline/render-plan"


def body():
    return {
        "timeline": {
            "version": 2,
            "tracks": [{"id": "v"}],
            "clips": [
                {
                    "id": "one",
                    "kind": "video",
                    "nodeId": "node",
                    "trackId": "v",
                    "durationMs": 1000,
                    "directMedia": {"storageKey": "resource:123"},
                }
            ],
        },
        "sources": [{"id": "node", "durationMs": 2000, "hasVideo": True, "hasAudio": False}],
    }


@pytest.fixture
def runtime():
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    state = {"actor": ActorContext(1, "test", "test@example.test", True, 1, "", "test")}
    opened = []

    def session():
        with Session() as value:
            opened.append(value)
            if state["actor"] is not None:
                value.info["actor"] = state["actor"]
            yield value
            assert not value.new and not value.dirty and not value.deleted
            assert not value.in_transaction()

    app.dependency_overrides[get_session] = session
    with TestClient(app) as client:
        yield client, state, opened


def test_authenticated_plan_is_side_effect_free_and_contains_only_opaque_sources(runtime):
    client, _, opened = runtime
    response = client.post(PATH, json=body(), headers={"X-Canvas-Actor": "1"})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["version"] == 1 and plan["durationMs"] == 1000
    assert plan["segments"][0]["sourceId"] == "node"
    assert plan["segments"][0]["hasAudio"] is False
    assert len(opened) == 1 and plan["audio"] is None and plan["subtitles"] is None
    assert "ffmpeg" not in response.text and "storageKey" not in response.text


def test_anonymous_plan_is_rejected_even_when_legacy_test_auth_is_disabled(runtime):
    client, state, _ = runtime
    state["actor"] = None
    response = client.post(PATH, json=body())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_captured_canvas_actor_cannot_change_during_compile(runtime):
    client, _, _ = runtime
    response = client.post(PATH, json=body(), headers={"X-Canvas-Actor": "2"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "canvas_actor_changed"


@pytest.mark.parametrize(
    "case, code",
    [
        ("overlap", "canvas_timeline_overlap"),
        ("missing_source", "canvas_timeline_missing_source"),
        ("text", "canvas_timeline_unsupported_kind"),
    ],
)
def test_compile_errors_preserve_business_reason_and_never_create_a_task(runtime, case, code):
    client, _, _ = runtime
    request = body()
    if case == "overlap":
        extra = deepcopy(request["timeline"]["clips"][0])
        extra.update(id="second", startMs=500)
        request["timeline"]["clips"].append(extra)
    elif case == "missing_source":
        request["sources"] = []
    else:
        request["timeline"]["clips"][0]["kind"] = "text"
    response = client.post(PATH, json=request)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == code


def test_fractional_milliseconds_fail_contract_validation_before_compilation(runtime):
    client, _, _ = runtime
    request = body()
    request["timeline"]["clips"][0]["durationMs"] = 1000.5
    response = client.post(PATH, json=request)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
