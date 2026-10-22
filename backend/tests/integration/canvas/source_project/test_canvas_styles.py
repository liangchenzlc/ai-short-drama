"""真实隔离 MySQL 中的账号风格、完整来源快照及并发版本。"""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)
from tests.unit.canvas.source_project.test_canvas_style import snapshot

pytestmark = pytest.mark.integration
ROOT = "/api/v1/canvas-runtime/style-profiles"


def test_personal_styles_persist_with_cas_import_identity_and_account_isolation(identity_app):
    owner, _ = account(identity_app, "styles_owner")
    other, _ = account(identity_app, "styles_other")
    value = snapshot()
    value.pop("coverUrl")
    value["assets"] = []
    body = {"profileJson": json.dumps(value), "import_key": "selected-local-style"}
    response = owner.post(ROOT, json=body)
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["rowVersion"] == "1"
    assert owner.post(ROOT, json=body).json()["profile"]["id"] == profile["id"]
    assert other.get(ROOT).json() == {"profiles": []}
    assert (
        other.patch(
            ROOT + "/" + profile["id"],
            json={"profileJson": body["profileJson"], "expected_row_version": "1"},
        ).status_code
        == 404
    )
    saved = owner.patch(
        ROOT + "/" + profile["id"],
        json={"profileJson": body["profileJson"], "expected_row_version": "1"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile"]["rowVersion"] == "2"
    stale = owner.patch(
        ROOT + "/" + profile["id"],
        json={"profileJson": body["profileJson"], "expected_row_version": "1"},
    )
    assert stale.status_code == 409
    assert (
        owner.get(ROOT).json()["profiles"][0]["profileJson"]
        == saved.json()["profile"]["profileJson"]
    )
    deleted = owner.request(
        "DELETE", ROOT + "/" + profile["id"], json={"expected_row_version": "2"}
    )
    assert deleted.status_code == 200, deleted.text
    assert owner.get(ROOT).json() == {"profiles": []}
    assert owner.post(ROOT, json=body).status_code == 409


def test_independent_sessions_merge_builtin_preferences(identity_app):
    owner, _ = account(identity_app, "style_preferences")
    second = TestClient(identity_app[0])
    second.cookies.update(owner.cookies)
    second.headers.update(owner.headers)
    requests = [
        (owner, {"preset_id": "style-a", "action": "favorite", "favorite": True}),
        (second, {"preset_id": "style-b", "action": "favorite", "favorite": True}),
    ]
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(lambda pair: pair[0].post(ROOT + "/preferences", json=pair[1]), requests)
        )
    assert all(item.status_code == 200 for item in responses), [item.text for item in responses]
    assert owner.get(ROOT + "/preferences").json()["favorites"] == ["style-a", "style-b"]
    used = second.post(ROOT + "/preferences", json={"preset_id": "style-a", "action": "use"})
    assert used.status_code == 200 and used.json()["recent"] == ["style-a"]
