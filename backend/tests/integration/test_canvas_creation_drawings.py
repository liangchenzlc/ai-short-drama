"""首次整图创建原子包含绘图，不能只交付图片预览。"""

import os
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import CanvasCreationResource, CanvasDrawingVersion, Project
from short_drama.service.canvas_drawing_service import CanvasDrawingService
from tests.integration.test_canvas_drawings import drawing_path, drawing_payload
from tests.integration.test_canvas_resources import ROOT, account, canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated canvas MinIO verification",
    ),
]


def creation_request(resource, key="drawing-complete-copy"):
    drawing = drawing_payload(
        previewResourceId=resource["id"],
        render={
            "resourceId": resource["id"],
            "storageKey": f"resource:{resource['id']}",
            "background": "white",
            "pageId": "page",
        },
    )["drawing"]
    drawing["snapshot"]["files"] = {
        "embedded": {"id": "embedded", "dataURL": f"{ROOT}/{resource['id']}/file"}
    }
    return {
        "source_key": key,
        "title": "带笔画的完整副本",
        "source_document": {
            "id": key,
            "workspaceProjectId": key,
            "revision": "0",
            "title": "带笔画的完整副本",
            "nodes": [
                {
                    "id": "drawing-node",
                    "type": "drawing",
                    "title": "绘图",
                    "position": {"x": 10, "y": 20},
                    "width": 360,
                    "height": 240,
                    "metadata": {
                        "drawingId": "sketch",
                        "drawingRevision": "1",
                        "drawingShapeCount": 1,
                    },
                }
            ],
            "connections": [],
        },
        "drawing_documents": [drawing],
    }


def test_first_creation_includes_precise_drawing_and_independent_media(resource_app):
    owner, _ = account(resource_app, "complete_drawing_owner")
    original = upload(owner, png()).json()["resource"]
    payload = creation_request(original)
    frozen = deepcopy(payload)
    headers = {"Idempotency-Key": "create-drawing-complete"}
    response = owner.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    copied = result["resource_map"][original["id"]]
    assert copied != original["id"]
    path = drawing_path({"id": result["source_key"]})
    saved = owner.get(path).json()["drawing"]
    expected = deepcopy(payload["drawing_documents"][0]["snapshot"])
    expected["files"]["embedded"]["dataURL"] = f"{ROOT}/{copied}/file"
    assert saved["snapshot"] == expected
    assert saved["revision"] == "1"
    assert saved["previewResourceId"] == copied
    assert saved["render"]["resourceId"] == copied
    assert saved["render"]["storageKey"] == f"resource:{copied}"
    assert owner.get(f"{ROOT}/{copied}/file").content == png()
    assert payload == frozen
    assert owner.post("/api/v1/canvas-workspace", headers=headers, json=payload).json() == result
    changed = deepcopy(payload)
    changed["drawing_documents"][0]["snapshot"]["elements"][0]["y"] = 99
    assert owner.post("/api/v1/canvas-workspace", headers=headers, json=changed).status_code == 409
    document_path = f"/api/v1/projects/{result['project_id']}/canvases/{result['id']}"
    document = owner.get(document_path + "/my-document").json()["source_document"]
    assert document["nodes"][0]["metadata"]["drawingRevision"] == "1"
    assert owner.get(path + "/versions/1").json()["drawing"] == saved


def test_drawing_failure_rolls_back_entire_creation_and_reuses_prepared_files(
    resource_app, monkeypatch
):
    owner, _ = account(resource_app, "complete_drawing_failure")
    original = upload(owner, png()).json()["resource"]
    payload = creation_request(original)
    append = CanvasDrawingService.append_version

    def fail_version(*args, **kwargs):
        append(*args, **kwargs)
        raise WorkflowError("injected_drawing_failure", "测试绘图事务回滚", 503)

    with resource_app[1]() as session:
        projects_before = session.scalar(select(func.count()).select_from(Project))
    monkeypatch.setattr(CanvasDrawingService, "append_version", fail_version)
    headers = {"Idempotency-Key": "create-drawing-rollback"}
    failed = owner.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert failed.status_code == 503, failed.text
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == projects_before
        assert session.scalar(select(func.count()).select_from(CanvasDrawingVersion)) == 0
        prepared = session.scalar(select(CanvasCreationResource))
        target_id = str(prepared.target_resource_id)
        assert prepared.status == "copied"
    monkeypatch.setattr(CanvasDrawingService, "append_version", append)
    retry = owner.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert retry.status_code == 201, retry.text
    assert retry.json()["resource_map"] == {original["id"]: target_id}
    assert owner.get(drawing_path({"id": retry.json()["source_key"]})).status_code == 200


def test_creation_rejects_incomplete_drawings_and_other_authors_private_media(resource_app):
    owner, _ = account(resource_app, "complete_drawing_guard")
    other, _ = account(resource_app, "complete_drawing_other")
    original = upload(owner, png()).json()["resource"]
    base = creation_request(original)
    invalid = []
    missing = deepcopy(base)
    missing.pop("drawing_documents")
    invalid.append(missing)
    duplicate = deepcopy(base)
    duplicate["drawing_documents"] *= 2
    invalid.append(duplicate)
    unbound = deepcopy(base)
    unbound["drawing_documents"][0]["drawingId"] = "unbound"
    invalid.append(unbound)
    old_version = deepcopy(base)
    old_version["drawing_documents"][0]["revision"] = "5"
    invalid.append(old_version)
    for index, payload in enumerate(invalid):
        rejected = owner.post(
            "/api/v1/canvas-workspace",
            headers={"Idempotency-Key": f"drawing-invalid-{index}"},
            json=payload,
        )
        assert rejected.status_code == 422, rejected.text
    denied = other.post(
        "/api/v1/canvas-workspace",
        headers={"Idempotency-Key": "drawing-other-private"},
        json=base,
    )
    assert denied.status_code == 404, denied.text
    project, _, _ = canvas(owner, "drawing-existing")
    payload = creation_request(original, "drawing-existing-copy")
    payload["source_document"]["workspaceProjectId"] = project["id"]
    response = owner.post(
        f"/api/v1/projects/{project['id']}/canvases",
        headers={"Idempotency-Key": "drawing-existing-copy"},
        json=payload,
    )
    assert response.status_code == 201, response.text
    assert owner.get(drawing_path({"id": response.json()["source_key"]})).status_code == 200
