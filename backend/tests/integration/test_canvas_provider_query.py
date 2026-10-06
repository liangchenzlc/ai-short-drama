"""原失败视频经真实 HTTP/供应商 GET parser/隔离 MySQL 与 MinIO 取回。"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select

from short_drama.ai import GenerationGateway
from short_drama.api.v1 import canvas_provider_tasks
from short_drama.core.exceptions import StorageUnavailable
from short_drama.dao.task_runtime_dao import LeaseLost, finish
from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasResult, MediaAsset, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.generation_archive import GenerationArchive
from tests.integration.test_canvas_generation_media_runtime import playable_fixture
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, records, seed
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
FROZEN_KEY = "controlled-frozen-video-key"
PROVIDER_ID = "original-video-task"


def update_key(client, identifier, key):
    path = "/api/v1/ai-model-configs/" + identifier
    current = client.get(path).json()
    updated = client.patch(path, json={"row_version": current["row_version"], "apikey": key})
    assert updated.status_code == 200, updated.text


def failed_video(app, name, *, anonymous=False, client=None, user=None, project=None):
    client, user, project, path, request = seed(
        app,
        name.replace("canvas_video_query_", "cvq_"),
        kind="video",
        client=client,
        user=user,
        project=project,
    )
    if not anonymous:
        update_key(client, request["logicalModelId"], FROZEN_KEY)
    task = admit(client, request)
    with app[1].begin() as session:
        row = session.get(AsyncTask, int(task["id"]))
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == row.id)
        )
        finish(row, "failed", {"code": "poll_failed", "message": "原查询失败"})
        call.provider_task_id = PROVIDER_ID
        call.status = "failed"
        call.finished_at = call.updated_at = utcnow()
        call.error = {"code": "poll_failed", "message": "原查询失败"}
    return client, user, project, path, request, task


def query_path(task):
    return f"{TASKS}/{task['id']}/query-provider"


def provider(
    app, monkeypatch, *, status="running", observation=None, anonymous=False, content=None
):
    """控制网络边界，仍执行真实 poll GET、认证头与 Ark 返回体解析。"""
    gateway = GenerationGateway(app[2])
    calls = []

    def response(method, url, snapshot, headers, body):
        calls.append((method, url))
        assert method == "GET" and body is None
        assert url == (
            "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/" + PROVIDER_ID
        )
        assert snapshot["budget_seconds"] <= 60
        assert headers == ({} if anonymous else {"Authorization": "Bearer " + FROZEN_KEY})
        if observation:
            observation()
        return {
            "id": PROVIDER_ID,
            "status": status,
            "content": {"video_url": "https://controlled-media.example.test/original.mp4"},
        }

    def download(url, limit):
        assert url == "https://controlled-media.example.test/original.mp4"
        assert content and limit >= len(content)
        return content, "video/mp4"

    monkeypatch.setattr(gateway, "_json_request", response)
    monkeypatch.setattr(gateway, "download_media", download)
    monkeypatch.setattr(canvas_provider_tasks, "GenerationGateway", lambda _settings: gateway)
    return calls


@pytest.mark.parametrize("status", ["running", "failed"])
def test_original_video_query_preserves_failure_and_never_creates_generation(
    identity_app, monkeypatch, status
):
    client, user, _, path, request, task = failed_video(
        identity_app, "canvas_video_query_" + status
    )
    before = client.get(path + "/my-document").json()
    update_key(client, request["logicalModelId"], "rotated-new-video-key")
    calls = provider(identity_app, monkeypatch, status=status)
    response = client.post(query_path(task))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recovered"] is False and result["task"]["status"] == "failed"
    assert result["providerStatus"] == ("processing" if status == "running" else "failed")
    assert result["task"]["id"] == task["id"]
    assert result["task"]["outputs"] == []
    assert FROZEN_KEY not in response.text and "rotated-new-video-key" not in response.text
    assert len(calls) == 1
    assert records(identity_app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert client.get(path + "/my-document").json() == before
    with identity_app[1]() as session:
        row = session.get(AsyncTask, int(task["id"]))
        assert row.lock_token is None and row.locked_until is None
        assert row.next_action is None and row.message_status == "idle"


def test_originally_anonymous_frozen_provider_remains_anonymous(identity_app, monkeypatch):
    client, _, _, _, _, task = failed_video(
        identity_app, "canvas_video_query_anonymous", anonymous=True
    )
    calls = provider(identity_app, monkeypatch, anonymous=True)
    response = client.post(query_path(task))
    assert response.status_code == 200 and response.json()["recovered"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("missing", ["unknown", "credentials", "unsafe-id", "unsupported"])
def test_unconfirmed_or_unavailable_original_provider_is_rejected_without_get(
    identity_app, monkeypatch, missing
):
    client, _, _, _, _, task = failed_video(identity_app, "canvas_video_query_missing_" + missing)
    with identity_app[1].begin() as session:
        call = session.scalar(select(AIGenerationRecord))
        if missing == "unknown":
            call.status, call.provider_task_id = "unknown", None
        elif missing == "credentials":
            call.credential_cipher = None
        elif missing == "unsafe-id":
            call.provider_task_id = "../other-task?key=private"
        else:
            call.adapter = "openai_responses.v1"
    calls = provider(identity_app, monkeypatch)
    response = client.post(query_path(task))
    assert response.status_code == 409, response.text
    assert calls == []
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "failed"


def test_nonfailed_and_other_authors_cannot_query_original_video(identity_app, monkeypatch):
    client, _, project, _, _, task = failed_video(identity_app, "canvas_video_query_author")
    member, other = account(identity_app, "canvas_video_query_reader")
    member.headers["X-Canvas-Actor"] = other["id"]
    join(identity_app, client, member, project["id"], other["id"])
    calls = provider(identity_app, monkeypatch)
    assert member.post(query_path(task)).status_code == 404
    with identity_app[1].begin() as session:
        row = session.get(AsyncTask, int(task["id"]))
        row.status, row.finished_at = "running", None
    assert client.post(query_path(task)).status_code == 409
    assert calls == []


def test_nonvideo_query_does_not_call_provider(identity_app, monkeypatch):
    client, _, _, _, request = seed(identity_app, "canvas_video_query_wrong_type")
    task = admit(client, request)
    with identity_app[1].begin() as session:
        finish(session.get(AsyncTask, int(task["id"])), "failed")
    calls = provider(identity_app, monkeypatch)
    assert client.post(query_path(task)).status_code == 409
    assert calls == []


def test_concurrent_original_query_owns_one_failed_lease_and_only_one_get(
    identity_app, monkeypatch
):
    client, _, _, _, _, task = failed_video(identity_app, "canvas_video_query_parallel")
    entered, release = Event(), Event()

    def observe():
        with identity_app[1]() as session:
            row = session.get(AsyncTask, int(task["id"]))
            assert row.status == "failed" and row.lock_token and row.next_action is None
        entered.set()
        assert release.wait(10)

    calls = provider(identity_app, monkeypatch, observation=observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, query_path(task))
        try:
            assert entered.wait(10)
            second = client.post(query_path(task))
            assert second.status_code == 409, second.text
            assert second.json()["error"]["code"] == "canvas_provider_query_busy"
        finally:
            release.set()
        result = first.result(timeout=15)
    assert result.status_code == 200, result.text
    assert len(calls) == 1


def test_revoke_during_get_cannot_write_observation_or_return_private_task(
    identity_app, monkeypatch
):
    owner, _, project, _, _ = seed(identity_app, "canvas_video_query_project_owner")
    member, user = account(identity_app, "canvas_video_query_revoked")
    join(identity_app, owner, member, project["id"], user["id"])
    client, _, _, _, _, task = failed_video(
        identity_app, "unused", client=member, user=user, project=project
    )

    def revoke():
        result = owner.delete(f"/api/v1/projects/{project['id']}/members/{user['id']}")
        assert result.status_code == 200, result.text

    calls = provider(identity_app, monkeypatch, observation=revoke)
    response = client.post(query_path(task))
    assert response.status_code == 404, response.text
    assert len(calls) == 1
    with identity_app[1]() as session:
        row = session.get(AsyncTask, int(task["id"]))
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == row.id)
        )
        assert row.status == "failed" and call.status == "failed"
        assert "canvas_provider_query" not in (call.response_data or {})
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0


def test_changed_operation_lease_after_get_cannot_finish_original_task(identity_app, monkeypatch):
    client, _, _, _, _, task = failed_video(identity_app, "canvas_video_query_changed")

    def change():
        with identity_app[1].begin() as session:
            row = session.get(AsyncTask, int(task["id"]))
            row.message_version += 1
            row.lock_token = row.locked_until = None

    calls = provider(identity_app, monkeypatch, observation=change)
    response = client.post(query_path(task))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "canvas_provider_query_changed"
    assert len(calls) == 1


def test_default_archive_cannot_save_a_terminal_task_without_special_ownership(identity_app):
    _, _, _, _, _, task = failed_video(identity_app, "canvas_video_query_default_archive")
    with identity_app[1]() as session:
        row = session.get(AsyncTask, int(task["id"]))
        call = session.scalar(select(AIGenerationRecord))
    archive = GenerationArchive(identity_app[1], identity_app[2], None, None)
    with pytest.raises(LeaseLost):
        archive.save_one(row, call, {"output_index": 1}, row.message_version, "not-a-query-lease")
    with identity_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 0


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
    reason="Enable actual isolated original-provider video MinIO verification",
)
@pytest.mark.parametrize("save_failure", [None, "lost-ack", "unavailable"])
def test_original_video_manifest_is_reused_after_save_failure_then_private_bind(
    resource_app, monkeypatch, tmp_path, save_failure
):
    client, user, project, path, request, task = failed_video(
        resource_app, "canvas_video_query_save_" + str(save_failure)
    )
    member, other = account(resource_app, "canvas_video_query_share")
    member.headers["X-Canvas-Actor"] = other["id"]
    join(resource_app, client, member, project["id"], other["id"])
    before = client.get(path + "/my-document").json()
    content = playable_fixture(tmp_path, "video")
    calls = provider(resource_app, monkeypatch, status="succeeded", content=content)
    storage = resource_app[0].state.storage
    original_put = storage.put
    puts = 0

    def put(*args, **kwargs):
        nonlocal puts
        puts += 1
        if save_failure == "unavailable" and puts == 1:
            raise StorageUnavailable("controlled outage before object write")
        result = original_put(*args, **kwargs)
        if save_failure == "lost-ack" and puts == 1:
            raise StorageUnavailable("controlled lost ACK after real object write")
        return result

    monkeypatch.setattr(storage, "put", put)
    response = client.post(query_path(task))
    if save_failure:
        assert response.status_code == 503, response.text
        with resource_app[1]() as session:
            call = session.scalar(select(AIGenerationRecord))
            manifest = call.response_data["media_manifest"]
            asset_identity = manifest[0]["asset_id"]
            assert not manifest[0].get("saved")
            row = session.get(AsyncTask, int(task["id"]))
            assert row.status == "failed" and row.lock_token is None
            assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        response = client.post(query_path(task))
    else:
        asset_identity = None
    assert response.status_code == 200, response.text
    result = response.json()
    current = result["task"]
    assert result["recovered"] is True and result["providerStatus"] == "succeeded"
    assert current["status"] == "succeeded" and current["resultState"] == "READY"
    assert current["id"] == task["id"] and len(calls) == 1
    assert records(resource_app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    media = json.loads(current["resultJson"])["video"]
    assert (media["width"], media["height"], media["durationMs"]) == (160, 90, 1000)
    assert media["bytes"] == len(content) and media["mimeType"] == "video/mp4"
    assert client.get(media["url"]).content == content
    assert member.get(media["url"]).status_code == 404
    assert client.get(path + "/my-document").json() == before
    with resource_app[1]() as session:
        archived = session.scalar(select(MediaFile))
        assert archived.checksum_sha256 == hashlib.sha256(content).hexdigest()
        assert archived.storage_locator.startswith("minio://canvas-test-")
        asset = session.scalar(select(MediaAsset))
        assert asset_identity is None or str(asset.id) == asset_identity
        assert session.scalar(select(CanvasResult)).attachment_status == "detached"
    repeated = client.post(query_path(task))
    assert repeated.status_code == 200 and repeated.json() == result
    assert len(calls) == 1 and puts == (2 if save_failure == "unavailable" else 1)
    bound = bind(client, request, current)
    assert bound["result"]["node"]["metadata"]["storageKey"] == media["storageKey"]
    assert member.get(media["url"]).content == content
    assert member.post(query_path(task)).status_code == 404


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
    reason="Enable actual isolated original-provider video MinIO verification",
)
def test_revocation_after_real_object_write_prevents_archive_rows_and_materialization(
    resource_app, monkeypatch, tmp_path
):
    owner, _, project, _, _ = seed(resource_app, "cvq_last_owner")
    member, user = account(resource_app, "cvq_last_author")
    join(resource_app, owner, member, project["id"], user["id"])
    client, _, _, path, _, task = failed_video(
        resource_app, "unused", client=member, user=user, project=project
    )
    before = owner.get(path).json()
    content = playable_fixture(tmp_path, "video")
    calls = provider(resource_app, monkeypatch, status="succeeded", content=content)
    storage = resource_app[0].state.storage
    original_put = storage.put
    writes = 0

    def revoke_after_put(*args, **kwargs):
        nonlocal writes
        result = original_put(*args, **kwargs)
        writes += 1
        revoked = owner.delete(f"/api/v1/projects/{project['id']}/members/{user['id']}")
        assert revoked.status_code == 200, revoked.text
        return result

    monkeypatch.setattr(storage, "put", revoke_after_put)
    response = client.post(query_path(task))
    assert response.status_code == 404, response.text
    assert len(calls) == writes == 1
    assert owner.get(path).json() == before
    with resource_app[1]() as session:
        assert session.get(AsyncTask, int(task["id"])).status == "failed"
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 0
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 0
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
