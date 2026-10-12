"""外部素材导入使用真实 HTTP、隔离 MySQL/MinIO 与原媒体检查；不调用模型。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import StorageUnavailable
from short_drama.domain import CanvasResourceUpload, MediaFile
from short_drama.service.video_render import executable
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_library_deletion import commit, image_node
from tests.integration.test_canvas_resources import ROOT, canvas, png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated external resource HTTP/MySQL/MinIO verification",
    ),
]


@pytest.fixture
def origin(resource_app):
    state = SimpleNamespace(
        data=png(),
        mime="image/png",
        requests=[],
        status=200,
        entered=threading.Event(),
        release=threading.Event(),
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_GET(self):
            state.requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "cookie": self.headers.get("Cookie"),
                }
            )
            if self.path.startswith("/hold"):
                state.entered.set()
                assert state.release.wait(15)
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", state.url.replace("127.0.0.1", "localhost"))
                self.end_headers()
                return
            self.send_response(state.status)
            self.send_header("Content-Type", state.mime)
            length = 50 * 1024**2 + 1 if self.path.startswith("/oversized") else len(state.data)
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if state.status == 200 and not self.path.startswith("/oversized"):
                self.wfile.write(state.data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_port}"
    state.url = state.base + "/reference.png?expires=expired-after-import"
    resource_app[0].state.settings = resource_app[2].model_copy(
        update={"model_discovery_allowed_hosts": ["127.0.0.1"]}
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        worker.join(5)
        assert not worker.is_alive()


def import_url(client, origin, document=None, *, key="external-import", kind="image", url=None):
    return client.post(
        ROOT + "/import",
        headers={
            "X-Idempotency-Key": key,
            **({"X-Canvas-Key": document["id"]} if document else {}),
        },
        json={"url": url or origin.url, "kind": kind, "width": 999, "height": 999},
    )


def uploads(resource_app):
    with resource_app[1]() as session:
        return list(session.scalars(select(CanvasResourceUpload)))


def test_import_freezes_original_bytes_and_ready_receipt_survives_source_expiry(
    resource_app, origin
):
    owner, _ = account(resource_app, "ri_owner")
    member, member_user = account(resource_app, "ri_member")
    outsider, _ = account(resource_app, "ri_other")
    project, path, document = canvas(owner, "import-publish")
    join(resource_app, owner, member, project["id"], member_user["id"])
    result = import_url(owner, origin, document)
    assert result.status_code == 200, result.text
    resource = result.json()["resource"]
    assert isinstance(resource["id"], str) and int(resource["id"]) > 2**53
    assert (resource["width"], resource["height"]) == (37, 19)
    assert resource["publicUrl"] == resource["objectKey"] == ""
    assert resource["etag"] == hashlib.sha256(png()).hexdigest()
    file = f"{ROOT}/{resource['id']}/file?proxy=1"
    assert owner.get(file).content == png()
    assert member.get(file).status_code == outsider.get(file).status_code == 404
    origin.status, origin.data = 404, png("blue")
    assert import_url(owner, origin, document).json() == result.json()
    assert len(origin.requests) == 1
    assert origin.requests[0]["authorization"] is origin.requests[0]["cookie"] is None
    conflict = import_url(owner, origin, document, url=origin.base + "/changed.png")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "canvas_upload_conflict"
    assert len(origin.requests) == 1
    document["nodes"] = [image_node(resource)]
    document["nodes"][0]["metadata"]["content"] = origin.url
    saved = commit(owner, path, document, "publish-import")
    shared = member.get(path + "/my-document").json()["source_document"]
    assert shared["nodes"][0]["metadata"]["storageKey"] == "resource:" + resource["id"]
    assert shared["nodes"][0]["metadata"]["content"] == file.partition("?")[0]
    assert member.get(file).content == png()
    partial = member.get(file, headers={"Range": "bytes=4-15"})
    assert partial.status_code == 206 and partial.content == png()[4:16]
    assert partial.headers["Content-Range"] == f"bytes 4-15/{len(png())}"
    assert partial.headers["Cache-Control"] == "no-store"
    assert owner.head(file).headers["Content-Length"] == str(len(png()))
    assert outsider.get(file).status_code == 404
    records = uploads(resource_app)
    assert len(records) == 1 and records[0].mode == "multipart" and records[0].status == "ready"
    assert origin.url not in str(records[0].declared_json)
    assert "expires=" not in str(saved)
    with resource_app[1]() as session:
        media = session.get(MediaFile, int(resource["id"]))
        assert media.project_id == int(project["id"]) and media.published_at
        location = resource_app[0].state.storage.stat(
            resource_app[2].minio_image_bucket, f"canvas/resources/{resource['id']}"
        )
        assert media.storage_locator == location.storage_locator
        with resource_app[0].state.storage.open(
            resource_app[2].minio_image_bucket, f"canvas/resources/{resource['id']}"
        ) as stream:
            assert stream.read() == png()


def test_import_recovers_original_reserved_object_after_lost_minio_ack_without_remote_get(
    resource_app, origin, monkeypatch
):
    owner, _ = account(resource_app, "ri_recover")
    storage = resource_app[0].state.storage
    original_put = storage.put
    writes = []
    fail = True

    def lose_ack(bucket, object_name, stream, size, mime):
        writes.append(object_name)
        original_put(bucket, object_name, stream, size, mime)
        if fail:
            raise StorageUnavailable("controlled lost MinIO receipt")

    monkeypatch.setattr(storage, "put", lose_ack)
    result = import_url(owner, origin)
    assert result.status_code == 503, result.text
    records = uploads(resource_app)
    assert len(records) == 1 and records[0].status == "pending"
    reserved = str(records[0].reserved_resource_id)
    origin.status = 404
    fail = False
    repeated = import_url(owner, origin)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["resource"]["id"] == reserved
    assert owner.get(f"{ROOT}/{reserved}/file").content == png()
    assert len(origin.requests) == 1 and len(set(writes)) == 1
    assert uploads(resource_app)[0].status == "ready"


def test_import_rejects_changed_bytes_after_unstored_attempt_and_preserves_original_identity(
    resource_app, origin, monkeypatch
):
    owner, _ = account(resource_app, "ri_changed")
    storage = resource_app[0].state.storage
    original_put = storage.put

    def fail_put(*_):
        raise StorageUnavailable("controlled unavailable storage")

    monkeypatch.setattr(storage, "put", fail_put)
    assert import_url(owner, origin).status_code == 503
    reserved = uploads(resource_app)[0].reserved_resource_id
    monkeypatch.setattr(storage, "put", original_put)
    origin.data = png("blue")
    changed = import_url(owner, origin)
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "canvas_upload_conflict"
    assert uploads(resource_app)[0].reserved_resource_id == reserved
    origin.data = png()
    recovered = import_url(owner, origin)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["resource"]["id"] == str(reserved)
    assert len(uploads(resource_app)) == 1


@pytest.mark.parametrize("kind", ["audio", "video", "file"])
def test_import_uses_real_media_probe_and_preserves_binary_bytes(
    resource_app, origin, tmp_path, kind
):
    owner, _ = account(resource_app, "ri_" + kind)
    if kind == "audio":
        data = BytesIO()
        with wave.open(data, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(bytes(16000))
        origin.data, origin.mime = data.getvalue(), "audio/wav"
        url = origin.base + "/sound.wav"
    elif kind == "video":
        path = tmp_path / "external.mp4"
        subprocess.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=blue:size=160x90:rate=24",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        origin.data, origin.mime = path.read_bytes(), "video/mp4"
        url = origin.base + "/external.mp4"
    else:
        origin.data, origin.mime = b"frozen canvas file\x00\x01", "application/octet-stream"
        url = origin.base + "/asset.bin"
    result = import_url(owner, origin, kind=kind, url=url)
    assert result.status_code == 200, result.text
    resource = result.json()["resource"]
    assert resource["kind"] == kind and resource["size"] == len(origin.data)
    assert resource["etag"] == hashlib.sha256(origin.data).hexdigest()
    assert (
        owner.get(f"{ROOT}/{resource['id']}/file?variant=playback&proxy=1").content == origin.data
    )
    if kind != "file":
        assert resource["durationMs"] == 1000
    if kind == "video":
        assert (resource["width"], resource["height"]) == (160, 90)


def test_import_checks_access_before_download_and_rechecks_revocation_before_storage(
    resource_app, origin
):
    owner, _ = account(resource_app, "ri_access_owner")
    member, member_user = account(resource_app, "ri_access_member")
    outsider, _ = account(resource_app, "ri_access_other")
    project, _, document = canvas(owner, "import-access")
    join(resource_app, owner, member, project["id"], member_user["id"])
    assert import_url(outsider, origin, document).status_code == 404
    assert origin.requests == []
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(import_url, member, origin, document, url=origin.base + "/hold.png")
        assert origin.entered.wait(10)
        revoked = owner.delete(f"/api/v1/projects/{project['id']}/members/{member_user['id']}")
        assert revoked.status_code in {200, 204}, revoked.text
        origin.release.set()
        result = pending.result(timeout=20)
    assert result.status_code == 404, result.text
    assert len(origin.requests) == 1 and uploads(resource_app) == []


def test_concurrent_import_reuses_one_resource_and_completed_request_skips_new_download(
    resource_app, origin
):
    owner, _ = account(resource_app, "ri_concurrent")
    _, _, document = canvas(owner, "import-concurrent")
    url = origin.base + "/hold-race.png"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(import_url, owner, origin, document, url=url)
        assert origin.entered.wait(10)
        second_started = threading.Event()

        def overlap():
            second_started.set()
            return import_url(owner, origin, document, url=url)

        second = pool.submit(overlap)
        assert second_started.wait(10)
        origin.release.set()
        responses = [first.result(timeout=20), second.result(timeout=20)]
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    assert len(uploads(resource_app)) == 1 and len(origin.requests) == 1
    assert import_url(owner, origin, document, url=url).json() == responses[0].json()
    assert len(origin.requests) == 1


def test_import_rejects_unsafe_url_redirect_credentials_empty_and_invalid_files(
    resource_app, origin
):
    owner, _ = account(resource_app, "ri_invalid")
    for url in (
        "file:///etc/passwd",
        origin.url + "#fragment",
        origin.url.replace("http://", "http://user:password@"),
        origin.url.replace("127.0.0.1", "localhost"),
    ):
        response = import_url(owner, origin, url=url)
        assert response.status_code == 422, response.text
    assert origin.requests == []
    response = import_url(owner, origin, url=origin.base + "/redirect.png")
    assert response.status_code == 422 and len(origin.requests) == 1
    response = owner.post(
        ROOT + "/import",
        headers={"X-Idempotency-Key": "forbidden-auth"},
        json={"url": origin.url, "kind": "image", "headers": {"Authorization": "test-only"}},
    )
    assert response.status_code == 422 and len(origin.requests) == 1
    assert (
        owner.post(ROOT + "/import", json={"url": origin.url, "kind": "image"}).status_code == 422
    )
    for data in (b"", b"invalid image"):
        origin.data = data
        response = import_url(owner, origin)
        assert response.status_code == 422, response.text
    response = import_url(owner, origin, url=origin.base + "/oversized.png")
    assert response.status_code == 413, response.text
    origin.status = 503
    response = import_url(owner, origin)
    assert response.status_code == 503, response.text
    assert uploads(resource_app) == []


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
    reason="Enable original canvas resource recovery browser verification",
)
@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("scenario", ["durable-media", "upload-unavailable", "lost-upload-receipt"])
def test_original_browser_preserves_media_identity_and_recovers_pending_image(
    resource_app, tmp_path, width, scenario
):
    owner, owner_user = account(resource_app, "recovery_owner")
    member, member_user = account(resource_app, "recovery_member")
    outsider, _ = account(resource_app, "recovery_outsider")
    project, path, document = canvas(owner, "resource-recovery-browser")
    join(resource_app, owner, member, project["id"], member_user["id"])
    media = [("image", "recovery-reference.png", "image/png", png())]
    if scenario == "durable-media":
        sound = BytesIO()
        with wave.open(sound, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(32000))
        video = tmp_path / "recovery.mp4"
        subprocess.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=blue:size=160x90:rate=24",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        media.extend(
            [
                ("audio", "recovery.wav", "audio/wav", sound.getvalue()),
                ("video", "recovery.mp4", "video/mp4", video.read_bytes()),
            ]
        )
    output = tmp_path / "browser"
    config = {
        "username": "recovery_owner",
        "password": PASSWORD,
        "userId": owner_user["id"],
        "otherUserId": member_user["id"],
        "sourceKey": document["id"],
        "path": path,
        "width": width,
        "scenario": scenario,
        "outputDirectory": str(output),
        "media": [
            {
                "kind": kind,
                "name": name,
                "mimeType": mime,
                "content": base64.b64encode(body).decode(),
            }
            for kind, name, mime, body in media
        ],
    }
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4196"})
    node = shutil.which("node")
    assert node, "Node.js is required for original canvas browser verification"
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [node, "scripts/verify-resource-recovery-python.mjs"],
            cwd=Path(__file__).resolve().parents[3] / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            stdout, errors = process.communicate(json.dumps(config), timeout=420)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            stdout, errors = process.communicate(timeout=10)
            pytest.fail(f"Resource recovery browser timed out: {errors}\n{stdout}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Resource recovery browser failed: {errors}\n{stdout}"
    report = json.loads(stdout)
    assert report["page_errors"] == [] and report["task_posts"] == 0
    assert report["cookies_only_recovery_without_reupload"]
    if scenario != "durable-media":
        assert report["pending_image_original_bytes_after_reload"]
        assert report["explicit_save_reused_upload_key"]
        if scenario == "lost-upload-receipt":
            assert report["explicit_save_reused_ready_identity"]
    saved = owner.get(path + "/my-document").json()["source_document"]
    shared = member.get(path + "/my-document").json()["source_document"]
    assert [node["id"] for node in saved["nodes"]] == [node["id"] for node in shared["nodes"]]
    assert len(saved["nodes"]) == len(media)
    original_resources = []
    for kind, _, _, body in media:
        item = next(node for node in saved["nodes"] if node["type"] == kind)
        identity = item["metadata"]["storageKey"].removeprefix("resource:")
        assert identity.isdecimal() and int(identity) > 2**53
        assert item["metadata"]["assetId"] and item["metadata"]["status"] == "success"
        member_node = next(node for node in shared["nodes"] if node["id"] == item["id"])
        assert member_node["type"] == item["type"]
        assert member_node["position"] == item["position"]
        assert member_node["metadata"]["storageKey"] == item["metadata"]["storageKey"]
        assert member_node["metadata"]["content"] == item["metadata"]["content"]
        assert "assetId" not in member_node["metadata"]
        file_path = f"{ROOT}/{identity}/file"
        assert item["metadata"]["content"] == file_path
        for client in (owner, member):
            assert client.get(file_path + "?proxy=1").content == body
            assert client.head(file_path).headers["Content-Length"] == str(len(body))
            partial = client.get(file_path, headers={"Range": "bytes=4-15"})
            assert partial.status_code == 206 and partial.content == body[4:16]
        assert outsider.get(file_path).status_code == 404
        with resource_app[1]() as session:
            resource = session.get(MediaFile, int(identity))
            assert resource.project_id == int(project["id"]) and resource.published_at
            assert resource.checksum_sha256 == hashlib.sha256(body).hexdigest()
            location = resource_app[0].state.storage.stat(
                getattr(resource_app[2], f"minio_{kind}_bucket"), f"canvas/resources/{identity}"
            )
            assert resource.storage_locator == location.storage_locator
            with resource_app[0].state.storage.open(
                location.bucket, location.object_name
            ) as stream:
                assert stream.read() == body
            identical = list(
                session.scalars(
                    select(MediaFile).where(
                        MediaFile.checksum_sha256 == resource.checksum_sha256,
                        MediaFile.format_code == resource.format_code,
                    )
                )
            )
            assert len(identical) == 1
        original_resources.append(
            {"id": identity, "kind": kind, "sha256": hashlib.sha256(body).hexdigest()}
        )
    assert "blob:" not in json.dumps(saved) and f"image:{owner_user['id']}:" not in json.dumps(
        saved
    )
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scenario": scenario,
                "width": width,
                "resources": original_resources,
                "browser": report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
