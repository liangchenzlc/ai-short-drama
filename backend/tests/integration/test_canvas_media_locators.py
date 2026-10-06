"""媒体展示定位的真实图保存、历史和首次复制验证。"""

import os
from copy import deepcopy

import pytest
from sqlalchemy import select

from short_drama.domain.canvas import CanvasNode, CanvasRevision
from tests.integration.test_canvas_creation import creation_request
from tests.integration.test_canvas_resource_copy import image_node, register
from tests.integration.test_canvas_resources import ROOT, account, canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.integration.test_identity_collaboration import join

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated canvas MinIO verification",
    ),
]


def save(client, path, document, key):
    result = client.post(
        path + "/commits",
        headers={"Idempotency-Key": key},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert result.status_code == 200, result.text
    return client.get(path + "/my-document").json()["source_document"]


def test_media_graph_snapshot_restore_and_legacy_cleanup_use_canonical_files(resource_app):
    owner, _ = account(resource_app, "locator_owner")
    member, other = account(resource_app, "locator_member")
    project, path, document = canvas(owner, "locator-project")
    join(resource_app, owner, member, project["id"], other["id"])
    resource = upload(owner, png(), source=document["id"]).json()["resource"]
    expected = f"{ROOT}/{resource['id']}/file"
    node = image_node(resource)
    node["metadata"].update(content="blob:local-preview", previewContent="blob:pending")
    node["metadata"]["prompt"] = "blob:private-prose"
    document["nodes"] = [node]
    document["timeline"] = {
        "clips": [
            {
                "id": "clip",
                "directMedia": {
                    "kind": "image",
                    "storageKey": f"resource:{resource['id']}",
                    "dataUrl": "data:image/png;base64,local-display-only",
                },
            }
        ]
    }
    saved = save(owner, path, document, "locator-graph")
    assert saved["nodes"][0]["metadata"]["content"] == expected
    assert "previewContent" not in saved["nodes"][0]["metadata"]
    assert saved["nodes"][0]["metadata"]["prompt"] == "blob:private-prose"
    assert saved["timeline"]["clips"][0]["directMedia"]["dataUrl"] == expected
    shared = member.get(path).json()["source_document"]
    assert shared["nodes"][0]["metadata"]["content"] == expected
    assert "prompt" not in shared["nodes"][0]["metadata"]
    assert member.get(expected).content == png()
    # A different preview URL for the same media cannot create another graph edit.
    same = deepcopy(saved)
    same["nodes"][0]["metadata"]["content"] = "blob:another-window"
    assert save(owner, path, same, "locator-same-file")["revision"] == saved["revision"]
    with resource_app[1].begin() as session:
        row = session.scalar(
            select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
        )
        assert row.content_json["metadata"]["content"] == expected
        # Emulate a pre-upgrade persisted display URL, without changing media identity.
        legacy = deepcopy(row.content_json)
        legacy["metadata"]["content"] = "blob:legacy-window"
        row.content_json = legacy
    assert (
        owner.get(path + "/my-document").json()["source_document"]["nodes"][0]["metadata"][
            "content"
        ]
        == expected
    )
    cleaned = save(owner, path, saved, "locator-clean-legacy")
    assert int(cleaned["revision"]) > int(saved["revision"])
    with resource_app[1]() as session:
        row = session.scalar(
            select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
        )
        assert row.content_json["metadata"]["content"] == expected
    old_revision = owner.get(path + "/revisions").json()["items"][-1]
    restored = owner.post(
        path + f"/revisions/{old_revision['id']}/restore",
        headers={"Idempotency-Key": "locator-restore-empty"},
        json={"expected_row_version": cleaned["revision"]},
    )
    assert restored.status_code == 200, restored.text
    history = owner.get(path + "/revisions").json()["items"][0]
    historic = owner.get(path + f"/revisions/{history['id']}").json()["source_document"]
    assert historic["nodes"][0]["metadata"]["content"] == expected
    with resource_app[1]() as session:
        snapshot = session.get(CanvasRevision, int(history["id"]))
        assert snapshot.snapshot_json["nodes"][0]["metadata"]["content"] == expected
    restored = owner.post(
        path + f"/revisions/{history['id']}/restore",
        headers={"Idempotency-Key": "locator-restore-media"},
        json={"expected_row_version": restored.json()["row_version"]},
    )
    assert restored.status_code == 200, restored.text
    final = owner.get(path + "/my-document").json()["source_document"]
    assert final["nodes"][0]["metadata"]["content"] == expected
    broken = deepcopy(final)
    broken["nodes"][0]["metadata"].update(storageKey="old-local-key", content="blob:orphan")
    rejected = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "locator-reject-orphan"},
        json={"expected_row_version": final["revision"], "source_document": broken},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_media_not_persisted"
    assert owner.get(path + "/my-document").json()["source_document"] == final


def test_initial_copy_canonicalizes_display_and_keeps_original_retry_identity(resource_app):
    owner, _ = account(resource_app, "locator_creation")
    resource = upload(owner, png()).json()["resource"]
    register(owner, "creation-original", resource)
    payload = creation_request(resource, "locator-created")
    payload["source_document"]["nodes"][0]["metadata"].update(content="blob:first-page")
    headers = {"Idempotency-Key": "locator-first-create"}
    created = owner.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    result = created.json()
    copied = result["resource_map"][resource["id"]]
    path = f"/api/v1/projects/{result['project_id']}/canvases/{result['id']}"
    saved = owner.get(path + "/my-document").json()["source_document"]
    assert saved["nodes"][0]["metadata"]["content"] == f"{ROOT}/{copied}/file"
    assert owner.get(f"{ROOT}/{copied}/file").content == png()
    assert owner.post("/api/v1/canvas-workspace", headers=headers, json=payload).json() == result
    changed = deepcopy(payload)
    changed["source_document"]["nodes"][0]["metadata"]["content"] = "blob:changed-request"
    response = owner.post("/api/v1/canvas-workspace", headers=headers, json=changed)
    assert response.status_code == 409, response.text
    assert payload["source_document"]["nodes"][0]["metadata"]["content"] == "blob:first-page"
