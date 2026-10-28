"""当前编辑器下载历史/私人草稿的真实浏览器、MySQL 与 MinIO 验证。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from short_drama.domain import MediaFile, ProjectCanvas
from short_drama.domain.canvas.document.canvas import (
    CanvasRevision,
    CanvasRevisionUserState,
    CanvasWriteReceipt,
)
from short_drama.service.base import utcnow
from short_drama.storage.models import ObjectLocation
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.generation.test_canvas_generation_media_runtime import (
    playable_fixture,
)
from tests.integration.canvas.resources.test_canvas_media_locators import save
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas, png, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable isolated MinIO and real canvas browser verification",
    ),
]


def media_node(resource, index, document):
    return {
        "id": f"old-{resource['kind']}",
        "type": resource["kind"],
        "title": f"旧版{resource['kind']}",
        "position": {"x": index * 300, "y": 80},
        "width": 240,
        "height": 160,
        "createdAt": document["createdAt"],
        "updatedAt": document["updatedAt"],
        "metadata": {
            "storageKey": "resource:" + resource["id"],
            "content": f"{ROOT}/{resource['id']}/file",
            "mimeType": resource["mimeType"],
            "durationMs": resource.get("durationMs"),
            "naturalWidth": resource.get("width"),
            "naturalHeight": resource.get("height"),
            "bytes": resource["size"],
            "nodeRole": "result",
            "resultOrigin": "upload",
            "manualSize": True,
            "prompt": "OWNER_HISTORY_PRIVATE",
        },
    }


def export_scene(resource_app, tmp_path, suffix):
    owner, owner_user = account(resource_app, "hx_o_" + suffix)
    other, other_user = account(resource_app, "hx_other_" + suffix)
    outsider, _ = account(resource_app, "hx_x_" + suffix)
    project, path, document = canvas(owner, "history-export-" + suffix)
    video = playable_fixture(tmp_path, "video")
    audio = playable_fixture(tmp_path, "audio")
    payloads = [
        ("image", "old.png", "image/png", png("red")),
        ("video", "old.mp4", "video/mp4", video),
        ("audio", "old.mp3", "audio/mpeg", audio),
        ("video", "timeline.mp4", "video/mp4", video),
        ("audio", "timeline.mp3", "audio/mpeg", audio),
        ("image", "current.png", "image/png", png("green")),
    ]
    media = []
    for index, (kind, name, mime, body) in enumerate(payloads):
        response = upload(
            owner,
            body,
            key=f"history-export-upload-{index}",
            source=document["id"],
            kind=kind,
            name=name,
            mime=mime,
        )
        assert response.status_code == 200, response.text
        resource = response.json()["resource"]
        assert isinstance(resource["id"], str) and int(resource["id"]) > 2**53
        media.append({"resource": resource, "body": body})
    document["nodes"] = [
        media_node(item["resource"], index, document) for index, item in enumerate(media[:3])
    ]
    document["connections"] = [
        {"id": "old-edge", "fromNodeId": "old-image", "toNodeId": "old-video"}
    ]
    document["timeline"] = {
        "version": 2,
        "durationMs": 1000,
        "tracks": [],
        "clips": [],
    }
    for item in media[3:5]:
        resource = item["resource"]
        kind = resource["kind"]
        document["timeline"]["tracks"].append(
            {"id": "track-" + kind, "kind": kind, "label": "旧版时间线", "order": 0}
        )
        document["timeline"]["clips"].append(
            {
                "id": "clip-" + kind,
                "nodeId": "timeline-only-" + kind,
                "kind": kind,
                "trackId": "track-" + kind,
                "startMs": 0,
                "durationMs": 1000,
                "prompt": "OWNER_TIMELINE_HISTORY_PRIVATE",
                "directMedia": {
                    "id": "timeline-only-" + kind,
                    "kind": kind,
                    "title": "旧版直连媒体",
                    "storageKey": "resource:" + resource["id"],
                    "url": f"{ROOT}/{resource['id']}/file",
                    "durationMs": resource["durationMs"],
                },
            }
        )
    old = save(owner, path, document, "history-export-old")
    with resource_app[1].begin() as session:
        for row in session.scalars(
            select(CanvasRevision).where(
                CanvasRevision.canvas_id == int(project["primary_canvas_id"])
            )
        ):
            row.created_at = utcnow() - timedelta(minutes=6)
    document = deepcopy(old)
    current = media_node(media[-1]["resource"], 0, document)
    current.update(id="current-image", title="当前图片")
    current["metadata"]["prompt"] = "OWNER_CURRENT_PRIVATE"
    document.update(nodes=[current], connections=[], timeline=None)
    current = save(owner, path, document, "history-export-current")
    history = owner.get(path + "/revisions").json()["items"]
    selected = next(item for item in history if item["row_version"] == old["revision"])
    history_path = path + f"/revisions/{selected['id']}"
    owner_history = owner.get(history_path).json()["source_document"]
    assert other.get(history_path).status_code == 404
    assert len(owner_history["nodes"]) == 3
    assert "OWNER_HISTORY_PRIVATE" in json.dumps(owner_history)
    draft = deepcopy(owner_history)
    draft["title"] += "-本人草稿"
    draft["nodes"][0]["title"] = "草稿旧图片"
    draft["nodes"][0]["metadata"]["prompt"] = "OWNER_LOCAL_DRAFT_PRIVATE"
    return {
        "owner": owner,
        "owner_user": owner_user,
        "other": other,
        "other_user": other_user,
        "outsider": outsider,
        "project": project,
        "path": path,
        "current": current,
        "selected": selected,
        "history_path": history_path,
        "owner_history": owner_history,
        "draft": draft,
        "media": media,
    }


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("actor,kind", [("owner", "cloud"), ("owner", "draft")])
def test_editor_downloads_frozen_history_or_private_draft_with_explicit_media_retry(
    resource_app, tmp_path, width, actor, kind
):
    suffix = f"{actor}_{kind}_{width}"
    scene = export_scene(resource_app, tmp_path, suffix)
    factory = resource_app[1]
    canvas_id = int(scene["project"]["primary_canvas_id"])
    with factory() as session:
        revision = session.get(CanvasRevision, int(scene["selected"]["id"]))
        original_snapshot = deepcopy(revision.snapshot_json)
        original_private = [
            (row.user_id, deepcopy(row.state_json))
            for row in session.scalars(
                select(CanvasRevisionUserState)
                .where(CanvasRevisionUserState.revision_id == revision.id)
                .order_by(CanvasRevisionUserState.user_id)
            )
        ]
        receipts = session.scalar(select(func.count()).select_from(CanvasWriteReceipt))
        revisions = session.scalar(select(func.count()).select_from(CanvasRevision))
    expected = scene["draft"] if kind == "draft" else scene[actor + "_history"]
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4189"})
    config = {
        "username": scene[actor + "_user"]["username"],
        "ownerUsername": scene["owner_user"]["username"],
        "otherUsername": scene["other_user"]["username"],
        "password": PASSWORD,
        "userId": scene[actor + "_user"]["id"],
        "ownerId": scene["owner_user"]["id"],
        "otherId": scene["other_user"]["id"],
        "path": scene["path"],
        "sourceKey": scene["current"]["id"],
        "historyId": scene["selected"]["id"],
        "historyRevision": scene["selected"]["row_version"],
        "currentRevision": scene["current"]["revision"],
        "expectedDocument": expected,
        "draftDocument": scene["draft"] if kind == "draft" else None,
        "width": width,
        "kind": kind,
        "actor": actor,
        "outputDirectory": str(tmp_path / "browser"),
        "media": [
            {
                "id": item["resource"]["id"],
                "mime": item["resource"]["mimeType"],
                "bytes": base64.b64encode(item["body"]).decode("ascii"),
            }
            for item in scene["media"][:5]
        ],
        "failureIds": [item["resource"]["id"] for item in scene["media"][3:5]],
        "currentResourceId": scene["media"][-1]["resource"]["id"],
    }
    with isolated_api(factory, settings, resource_app[0].state.storage) as api_url:
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-history-export-python.mjs"],
            cwd=Path(__file__).resolve().parents[5] / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=240)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"History export browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"History export browser failed: {errors}\n{output}"
    report = json.loads(output)
    assert report["step"] == "complete"
    assert report["page_errors"] == report["unexpected_failures"] == report["writes"] == []
    assert report["download_count"] == 1
    assert report["incomplete_downloads"] == 0
    assert report["frozen_media_bytes"] and report["current_graph_unchanged"]
    assert report["explicit_retry"] and report["history_read_only"] and report["scope_isolation"]
    with factory() as session:
        assert session.get(ProjectCanvas, canvas_id).row_version == int(
            scene["current"]["revision"]
        )
        revision = session.get(CanvasRevision, int(scene["selected"]["id"]))
        assert revision.snapshot_json == original_snapshot
        assert [
            (row.user_id, row.state_json)
            for row in session.scalars(
                select(CanvasRevisionUserState)
                .where(CanvasRevisionUserState.revision_id == revision.id)
                .order_by(CanvasRevisionUserState.user_id)
            )
        ] == original_private
        assert session.scalar(select(func.count()).select_from(CanvasWriteReceipt)) == receipts
        assert session.scalar(select(func.count()).select_from(CanvasRevision)) == revisions
        for item in scene["media"]:
            resource = item["resource"]
            media = session.get(MediaFile, int(resource["id"]))
            assert media.checksum_sha256 == hashlib.sha256(item["body"]).hexdigest()
            assert media.storage_locator.startswith("minio://canvas-test-")
            location = ObjectLocation.parse(
                media.storage_locator, {getattr(settings, f"minio_{resource['kind']}_bucket")}
            )
            with resource_app[0].state.storage.open(
                location.bucket, location.object_name
            ) as content:
                assert content.read() == item["body"]
    assert (
        scene["owner"].get(scene["history_path"]).json()["source_document"]
        == scene["owner_history"]
    )
    assert scene["other"].get(scene["history_path"]).status_code == 404
    assert scene["outsider"].get(scene["history_path"]).status_code == 404
    after = scene["owner"].get(scene["path"] + "/my-document").json()["source_document"]
    for key in ("nodes", "connections", "timeline", "revision"):
        assert after.get(key) == scene["current"].get(key)
    for item in scene["media"]:
        resource_path = f"{ROOT}/{item['resource']['id']}/file"
        for reader in (scene["owner"],):
            response = reader.get(resource_path)
            assert response.status_code == 200, response.text
            assert response.content == item["body"]
        assert scene["other"].get(resource_path).status_code == 404
        assert scene["outsider"].get(resource_path).status_code == 404
