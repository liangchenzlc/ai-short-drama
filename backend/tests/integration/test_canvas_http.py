"""Canvas HTTP contracts use real accounts, CSRF middleware and MySQL transactions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select

from short_drama.db import access
from short_drama.domain.canvas import CanvasUserState
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_identity_collaboration import account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("waiting_for_graph", [False, True])
def test_first_personal_writes_use_current_state_after_waiting_for_project_lock(
    identity_app, database_errors, monkeypatch, waiting_for_graph
):
    client, _ = account(identity_app, "canvas_personal_mvcc")
    project = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "personal-mvcc"},
        json={"name": "个人状态并发", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = client.get(path + "/my-document").json()["source_document"]
    document["title"] = "并发时保存作品"
    original = access.require_project
    entered, other_committed = Event(), Event()

    def snapshot_before_lock(session, project_id, **options):
        if options.get("lock") and not entered.is_set():
            # Establish an empty REPEATABLE READ snapshot, just as a scoped
            # authorization read can before the project write lock is acquired.
            assert session.scalar(select(CanvasUserState)) is None
            entered.set()
            assert other_committed.wait(10)
        return original(session, project_id, **options)

    monkeypatch.setattr(access, "require_project", snapshot_before_lock)
    viewport = {"x": 12, "y": 24, "k": 1.25}
    expected = {"x": 0, "y": 0, "k": 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = (
            pool.submit(
                client.post,
                path + "/commits",
                headers={"Idempotency-Key": "graph-after-state"},
                json={"expected_row_version": document["revision"], "source_document": document},
            )
            if waiting_for_graph
            else pool.submit(
                client.put,
                path + "/viewport",
                json={"expected_viewport": expected, "viewport": viewport},
            )
        )
        assert entered.wait(10)
        try:
            saved = client.put(
                path + "/view-preferences",
                json={
                    "expected_preferences": {"backgroundMode": "dots", "showImageInfo": False},
                    "preferences": {"backgroundMode": "lines", "showImageInfo": True},
                },
            )
            assert saved.status_code == 200, saved.text
        finally:
            other_committed.set()
        result = waiting.result()
    assert result.status_code == 200, (result.text, database_errors)
    state = client.get(path + "/my-document").json()["source_document"]
    assert state["viewport"] == (expected if waiting_for_graph else viewport)
    if waiting_for_graph:
        assert state["title"] == document["title"]
    assert state["backgroundMode"] == "lines" and state["showImageInfo"] is True
    assert database_errors == []


def test_authenticated_canvas_save_conflict_history_and_delete_receipt(identity_app):
    client, user = account(identity_app, "canvas_http")
    client.headers["X-Canvas-Actor"] = user["id"]
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "http-project"},
        json={"name": "画布 HTTP 验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    invalid_episode = client.post(
        f"/api/v1/projects/{project['id']}/episodes", json={"title": "不创建伪分集"}
    )
    assert invalid_episode.status_code == 409
    assert invalid_episode.json()["error"]["code"] == "standard_mode_required"
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    read = client.get(path + "/my-document")
    assert read.status_code == 200, read.text
    canvas = read.json()
    document = canvas["source_document"]
    assert isinstance(document["revision"], str)
    document["nodes"] = [
        {
            "id": "note",
            "type": "text",
            "title": "文字",
            "position": {"x": 10.125, "y": -12.375},
            "width": 320,
            "height": 220,
            "metadata": {"content": "真实数据库", "prompt": "私人指令"},
        }
    ]
    payload = {"expected_row_version": canvas["row_version"], "source_document": document}
    headers = {"Idempotency-Key": "http-save"}
    saved = client.post(path + "/commits", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    assert client.post(path + "/commits", json=payload, headers=headers).json() == saved.json()
    conflict = client.post(
        path + "/commits", json=payload, headers={"Idempotency-Key": "http-stale"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_revision_conflict"
    assert client.get(path).json()["source_document"]["nodes"][0]["metadata"] == {
        "content": "真实数据库"
    }
    revisions = client.get(path + "/revisions").json()["items"]
    assert revisions and revisions[0]["payload_bytes"] > 0
    deleted = client.request(
        "DELETE",
        path,
        headers={"Idempotency-Key": "http-delete"},
        json={"expected_row_version": saved.json()["row_version"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["project_archived"]
    assert client.get(path).status_code == 404
    receipt = client.get("/api/v1/canvas-write-receipts/http-delete")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["result"] == deleted.json()


def test_canvas_actor_switch_stops_request_without_revoking_new_session(identity_app):
    client, user = account(identity_app, "canvas_switch")
    result = client.get("/api/v1/canvas-workspace", headers={"X-Canvas-Actor": "1"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "canvas_actor_changed"
    assert client.get("/api/v1/auth/me").json()["user"]["id"] == user["id"]
    assert (
        client.get("/api/v1/canvas-workspace", headers={"X-Canvas-Actor": user["id"]}).status_code
        == 200
    )


def test_viewport_compare_and_swap_does_not_modify_graph_or_overwrite_another_window(identity_app):
    client, user = account(identity_app, "canvas_viewport")
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "viewport-project"},
        json={"name": "视口独立保存", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{created['id']}/canvases/{created['primary_canvas_id']}"
    document = client.get(path + "/my-document").json()
    original = document["source_document"]["viewport"]
    moved = {"x": 12.125, "y": -73.75, "k": 0.625}
    payload = {"expected_viewport": original, "viewport": moved}
    saved = client.put(path + "/viewport", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["viewport"] == moved
    assert client.put(path + "/viewport", json=payload).json() == saved.json()
    assert client.get(path).json()["row_version"] == document["row_version"]
    conflict = client.put(path + "/viewport", json={**payload, "viewport": {**moved, "x": 99}})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_viewport_conflict"
    # A graph saved from an older viewport must preserve the separately acknowledged position.
    stale_graph = document["source_document"]
    stale_graph["nodes"] = [
        {
            "id": "note",
            "type": "text",
            "title": "内容",
            "position": {"x": 0, "y": 0},
            "width": 300,
            "height": 200,
            "metadata": {"content": "保存正文"},
        }
    ]
    committed = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "viewport-graph"},
        json={"expected_row_version": document["row_version"], "source_document": stale_graph},
    )
    assert committed.status_code == 200, committed.text
    assert client.get(path + "/my-document").json()["source_document"]["viewport"] == moved


def test_view_preferences_persist_independently_and_stale_graph_cannot_reset_them(identity_app):
    client, _ = account(identity_app, "canvas_appearance")
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "appearance-project"},
        json={"name": "外观独立保存", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{created['id']}/canvases/{created['primary_canvas_id']}"
    original = client.get(path + "/my-document").json()
    old = {"appearance": None, "backgroundMode": "dots", "showImageInfo": False}
    desired = {"appearance": {"mode": "light"}, "backgroundMode": "lines", "showImageInfo": True}
    payload = {"expected_preferences": old, "preferences": desired}
    saved = client.put(path + "/view-preferences", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["preferences"]["appearance"]["mode"] == "light"
    assert client.put(path + "/view-preferences", json=payload).json() == saved.json()
    assert client.get(path).json()["row_version"] == original["row_version"]
    assert client.get(path + "/revisions").json()["items"] == []
    stale = original["source_document"]
    stale["title"] = "旧请求的新正文标题"
    saved_graph = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "appearance-graph"},
        json={"expected_row_version": original["row_version"], "source_document": stale},
    )
    assert saved_graph.status_code == 200, saved_graph.text
    reread = client.get(path + "/my-document").json()["source_document"]
    assert {key: reread[key] for key in desired} == desired
    assert all(key not in client.get(path).json()["source_document"] for key in desired)
    conflict = client.put(path + "/view-preferences", json={**payload, "preferences": old})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_view_preferences_conflict"
    viewport = client.put(
        path + "/viewport",
        json={
            "expected_viewport": reread["viewport"],
            "viewport": {"x": 1.5, "y": 2, "k": 1.2},
        },
    )
    assert viewport.status_code == 200
    assert (
        client.put(
            path + "/view-preferences",
            json={
                "expected_preferences": desired,
                "preferences": {**desired, "backgroundMode": "blank"},
            },
        ).status_code
        == 200
    )
    for invalid in (
        {"appearance": {"mode": "custom"}},
        {"backgroundMode": "unknown"},
        {"appearance": {"mode": "dark", "apiKey": "not-a-secret"}},
    ):
        rejected = client.put(
            path + "/view-preferences",
            json={
                "expected_preferences": desired,
                "preferences": {**desired, **invalid},
            },
        )
        assert rejected.status_code == 422, rejected.text
