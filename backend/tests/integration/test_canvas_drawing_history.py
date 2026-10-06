"""绘图历史与整图恢复使用真实事务，不能混用最新子文档。"""

import os
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import CanvasDrawing, CanvasDrawingVersion, CanvasRevisionDrawingReference
from short_drama.domain.canvas import CanvasRevision
from short_drama.service.base import utcnow
from tests.integration.test_canvas_drawings import drawing_path, drawing_payload
from tests.integration.test_canvas_media_locators import save
from tests.integration.test_canvas_resources import ROOT, account, canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.integration.test_identity_collaboration import join

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated drawing history verification",
    ),
]


def history_scene(resource_app, name):
    client, _ = account(resource_app, name)
    project, path, document = canvas(client, name)
    resource = upload(client, png(), source=document["id"]).json()["resource"]
    request = drawing_payload(previewResourceId=resource["id"])
    drawing = client.put(drawing_path(document), json=request)
    assert drawing.status_code == 200, drawing.text
    document["nodes"] = [
        {
            "id": "drawing-node",
            "type": "drawing",
            "title": "绘图",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"drawingId": "sketch", "drawingRevision": "1", "drawingShapeCount": 1},
        }
    ]
    document = save(client, path, document, name + "-graph")
    with resource_app[1].begin() as session:
        for row in session.scalars(
            select(CanvasRevision).where(
                CanvasRevision.canvas_id == int(project["primary_canvas_id"])
            )
        ):
            row.created_at = utcnow() - timedelta(minutes=6)
    # The subdocument is saved first, then its graph summary, as in the original editor.
    changed = drawing_payload("1", snapshot={"elements": [{"id": "new", "type": "ellipse"}]})
    result = client.put(drawing_path(document), json=changed)
    assert result.status_code == 200, result.text
    document["nodes"][0]["metadata"]["drawingRevision"] = "2"
    document = save(client, path, document, name + "-graph-2")
    history = client.get(path + "/revisions").json()
    original = next(item for item in history["items"] if item["node_count"] == 1)
    return client, project, path, document, request, original, resource


def restore(client, path, history, expected, key, drawing_heads):
    return client.post(
        path + f"/revisions/{history['id']}/restore",
        headers={"Idempotency-Key": key},
        json={"expected_row_version": expected, "expected_drawing_heads": drawing_heads},
    )


def test_history_freezes_drawing_preview_and_restores_deleted_strokes_atomically(resource_app):
    client, project, path, document, original, history, resource = history_scene(
        resource_app, "drawing_history"
    )
    member, user = account(resource_app, "drawing_history_member")
    outsider, _ = account(resource_app, "drawing_history_outsider")
    join(resource_app, client, member, project["id"], user["id"])
    historic = member.get(path + f"/revisions/{history['id']}").json()["source_document"]
    metadata = historic["nodes"][0]["metadata"]
    assert metadata["drawingRevision"] == "1"
    assert metadata["drawingPreviewStorageKey"] == f"resource:{resource['id']}"
    assert member.get(metadata["drawingPreviewUrl"]).content == png()
    assert outsider.get(path + f"/revisions/{history['id']}").status_code == 404
    assert client.delete(drawing_path(document)).status_code == 200
    assert client.get(drawing_path(document)).status_code == 404
    heads = member.get(path + "/revisions").json()["drawing_heads"]
    assert heads == {"sketch": {"revision": "2", "deleted": True}}
    result = restore(member, path, history, document["revision"], "drawing-history-restore", heads)
    assert result.status_code == 200, result.text
    restored = member.get(drawing_path(document)).json()["drawing"]
    assert restored["revision"] == "3"
    assert restored["snapshot"] == original["drawing"]["snapshot"]
    graph = member.get(path + "/my-document").json()["source_document"]
    assert graph["nodes"][0]["metadata"]["drawingRevision"] == "3"
    assert graph["nodes"][0]["metadata"]["drawingPreviewStorageKey"] == f"resource:{resource['id']}"
    assert (
        restore(
            member, path, history, document["revision"], "drawing-history-restore", heads
        ).json()
        == result.json()
    )
    assert member.get(drawing_path(document)).json()["drawing"] == restored
    assert member.get(path + f"/revisions/{history['id']}").json()["source_document"] == historic
    assert member.get(f"{ROOT}/{resource['id']}/file").content == png()


def test_drawing_change_after_history_list_rejects_restore_without_losing_either_version(
    resource_app,
):
    client, _, path, document, _, history, _ = history_scene(resource_app, "drawing_history_cas")
    heads = client.get(path + "/revisions").json()["drawing_heads"]
    latest = client.put(drawing_path(document), json=drawing_payload("2", shapeCount=3))
    assert latest.status_code == 200, latest.text
    conflict = restore(client, path, history, document["revision"], "drawing-head-conflict", heads)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "canvas_drawing_restore_conflict"
    assert client.get(path + "/my-document").json()["source_document"] == document
    assert client.get(drawing_path(document)).json() == latest.json()
    refreshed = client.get(path + "/revisions").json()["drawing_heads"]
    accepted = restore(
        client, path, history, document["revision"], "drawing-head-refreshed", refreshed
    )
    assert accepted.status_code == 200, accepted.text


def test_failed_restore_rolls_back_graph_drawing_versions_and_receipt(resource_app, monkeypatch):
    from short_drama.service.canvas_service import CanvasService

    client, project, path, document, _, history, _ = history_scene(
        resource_app, "drawing_restore_fail"
    )
    heads = client.get(path + "/revisions").json()["drawing_heads"]
    before = client.get(drawing_path(document)).json()
    original = CanvasService.record_write

    def fail_record(self, **values):
        original(self, **values)
        if values["operation"] == "canvas.restore":
            raise RuntimeError("injected restore receipt failure")

    monkeypatch.setattr(CanvasService, "record_write", fail_record)
    with pytest.raises(RuntimeError, match="injected restore receipt failure"):
        restore(client, path, history, document["revision"], "drawing-atomic-restore", heads)
    assert client.get(path + "/my-document").json()["source_document"] == document
    assert client.get(drawing_path(document)).json() == before
    with resource_app[1]() as session:
        drawing = session.scalar(
            select(CanvasDrawing).where(
                CanvasDrawing.canvas_id == int(project["primary_canvas_id"])
            )
        )
        assert [
            row.row_version
            for row in session.scalars(
                select(CanvasDrawingVersion)
                .where(CanvasDrawingVersion.drawing_id == drawing.id)
                .order_by(CanvasDrawingVersion.row_version)
            )
        ] == [1, 2]
    monkeypatch.setattr(CanvasService, "record_write", original)
    assert (
        restore(
            client, path, history, document["revision"], "drawing-atomic-restore", heads
        ).status_code
        == 200
    )


def test_legacy_history_uses_only_its_explicit_drawing_version(resource_app):
    client, project, path, document, _, history, _ = history_scene(
        resource_app, "drawing_legacy_history"
    )
    with resource_app[1].begin() as session:
        row = session.get(CanvasRevision, int(history["id"]))
        payload = deepcopy(row.snapshot_json)
        payload["nodes"][0]["metadata"]["drawingRevision"] = "999"
        row.snapshot_json = payload
    heads = client.get(path + "/revisions").json()["drawing_heads"]
    rejected = restore(
        client, path, history, document["revision"], "drawing-missing-version", heads
    )
    assert rejected.status_code == 409, rejected.text
    assert client.get(path + "/my-document").json()["source_document"] == document


def test_history_drawing_bindings_protect_versions_and_are_immutable(resource_app):
    _, project, _, _, _, history, _ = history_scene(resource_app, "drawing_bound_versions")
    with resource_app[1]() as session:
        binding = session.scalar(
            select(CanvasRevisionDrawingReference).where(
                CanvasRevisionDrawingReference.revision_id == int(history["id"])
            )
        )
        identifier, binding_id = binding.drawing_version_id, binding.id
        owner = session.get(CanvasDrawingVersion, identifier).created_by
    for model, row_id, field, value in (
        (CanvasDrawingVersion, identifier, "document_json", {}),
        (CanvasRevisionDrawingReference, binding_id, "drawing_version_id", identifier + 1),
    ):
        with resource_app[1]() as session, pytest.raises(WorkflowError) as failure:
            session.info["actor"] = SimpleNamespace(user_id=owner)
            with session.begin():
                row = session.get(model, row_id)
                setattr(row, field, value)
                session.flush()
        assert failure.value.code == "canvas_drawing_version_immutable"
    # Exercise the actual MySQL RESTRICT and snapshot-owned CASCADE constraints.
    with resource_app[1]() as session, pytest.raises(IntegrityError):
        with session.begin():
            session.delete(session.get(CanvasDrawingVersion, identifier))
            session.flush()
    with resource_app[1].begin() as session:
        session.delete(session.get(CanvasRevision, int(history["id"])))
    with resource_app[1].begin() as session:
        assert session.get(CanvasRevisionDrawingReference, binding_id) is None
        session.delete(session.get(CanvasDrawingVersion, identifier))
    with resource_app[1]() as session:
        drawing = session.scalar(
            select(CanvasDrawing).where(
                CanvasDrawing.canvas_id == int(project["primary_canvas_id"])
            )
        )
        assert drawing.row_version == 2
