"""Private library classification uses real authenticated HTTP and isolated MySQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import event, select

from short_drama.dao.canvas_library_dao import CanvasLibraryDAO
from short_drama.domain import CanvasLibraryAsset, CanvasLibraryFolderItem
from short_drama.service.canvas_service import CanvasService
from tests.integration.test_identity_collaboration import account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
ASSETS = "/api/v1/canvas-runtime/assets"
FOLDERS = "/api/v1/canvas-runtime/asset-folders"


@pytest.fixture
def database_errors(identity_app):
    errors = []
    engine = identity_app[1].kw["bind"]

    def record_error(context):
        errors.append(str(context.original_exception))

    event.listen(engine, "handle_error", record_error)
    try:
        yield errors
    finally:
        event.remove(engine, "handle_error", record_error)


def text_asset(client, key, *, folder_id="", status="confirmed"):
    asset = {
        "id": key,
        "kind": "text",
        "title": "分类验证 " + key,
        "data": {"content": "保留原文"},
        "folderId": folder_id,
        "status": status,
    }
    response = client.put(ASSETS + "/" + key, json={"asset": asset})
    assert response.status_code == 200, response.text
    return asset


def test_folder_names_private_scope_move_filters_and_delete_preserve_assets(
    identity_app, database_errors
):
    owner, _ = account(identity_app, "canvas_folders_owner")
    other, _ = account(identity_app, "canvas_folders_other")
    for name in ("  ", "字" * 41):
        assert owner.post(FOLDERS, json={"name": name}).status_code == 422
    response = owner.post(FOLDERS, json={"name": "  My 分类  "})
    assert response.status_code == 200, response.text
    folder = response.json()["folder"]
    identifier = folder["id"]
    assert isinstance(identifier, str) and int(identifier) > 2**53
    assert folder["name"] == "My 分类" and folder["position"] == 0
    assert owner.post(FOLDERS, json={"name": "my 分类"}).status_code == 409
    assert other.get(FOLDERS).json() == {"folders": []}
    assert other.patch(FOLDERS + "/" + identifier, json={"name": "偷改"}).status_code == 404
    assert other.delete(FOLDERS + "/" + identifier).status_code == 404
    assert other.post(FOLDERS, json={"name": "my 分类"}).status_code == 200
    renamed = owner.patch(FOLDERS + "/" + identifier, json={"name": "工作素材"})
    assert renamed.status_code == 200, renamed.text
    text_asset(owner, "one", folder_id=identifier)
    text_asset(owner, "two")
    text_asset(other, "foreign")
    failed = owner.patch(
        ASSETS + "/folder", json={"assetIds": ["two", "foreign"], "folderId": identifier}
    )
    assert failed.status_code == 404, failed.text
    assert owner.get(ASSETS + "/two").json()["asset"]["folderId"] == ""
    moved = owner.patch(
        ASSETS + "/folder", json={"assetIds": [" two ", "two"], "folderId": identifier}
    )
    assert moved.status_code == 200 and moved.json() == {
        "assetIds": ["two"],
        "folderId": identifier,
    }
    page = owner.get(ASSETS, params={"page": 1, "folderId": identifier})
    assert page.status_code == 200, (page.text, database_errors)
    assert page.json()["total"] == 2 and page.json()["folderCounts"] == {identifier: 2}
    assert all(item["folderId"] == identifier for item in page.json()["assets"])
    assert owner.get(ASSETS, params={"page": 1, "uncategorized": 1}).json()["total"] == 0
    assert (
        owner.patch(ASSETS + "/folder", json={"assetIds": [], "folderId": identifier}).status_code
        == 422
    )
    assert owner.delete(FOLDERS + "/" + identifier).status_code == 200
    assert owner.get(FOLDERS).json() == {"folders": []}
    page = owner.get(ASSETS, params={"page": 1, "uncategorized": 1})
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 2 and page.json()["folderCounts"] == {"": 2}
    assert {item["data"]["content"] for item in page.json()["assets"]} == {"保留原文"}
    assert (
        owner.patch(
            ASSETS + "/folder", json={"assetIds": ["one"], "folderId": identifier}
        ).status_code
        == 404
    )
    assert owner.get(ASSETS + "/one").json()["asset"]["folderId"] == ""
    with identity_app[1]() as session:
        assert session.scalar(select(CanvasLibraryFolderItem.id)) is None
        assert all(
            "folderId" not in row.payload_json
            for row in session.scalars(select(CanvasLibraryAsset))
        )


def test_concurrent_folder_creation_is_unique_and_move_delete_has_no_dangling_assignment(
    identity_app,
):
    owner, _ = account(identity_app, "canvas_folders_race")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: owner.post(FOLDERS, json={"name": "并发分类"}), range(2))
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    folder_id = next(
        response.json()["folder"]["id"] for response in responses if response.status_code == 200
    )
    text_asset(owner, "moving")
    with ThreadPoolExecutor(max_workers=2) as pool:
        move = pool.submit(
            owner.patch, ASSETS + "/folder", json={"assetIds": ["moving"], "folderId": folder_id}
        )
        deletion = pool.submit(owner.delete, FOLDERS + "/" + folder_id)
        moved, deleted = move.result(), deletion.result()
    assert moved.status_code in {200, 404}, moved.text
    assert deleted.status_code == 200, deleted.text
    assert owner.get(ASSETS + "/moving").json()["asset"]["folderId"] == ""


def test_library_write_overlaps_canvas_autosave_without_parent_lock_deadlock(
    identity_app, database_errors, monkeypatch
):
    owner, _ = account(identity_app, "canvas_library_autosave")
    project = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "library-autosave"},
        json={"name": "并发保存", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = owner.get(path + "/my-document").json()["source_document"]
    document["nodes"] = [
        {
            "id": "parallel-text",
            "type": "text",
            "title": "并发节点",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 200,
            "metadata": {"content": "正文"},
        }
    ]
    library_ready, graph_ready = Event(), Event()
    read_asset = CanvasLibraryDAO.asset
    snapshot = CanvasService._snapshot

    def synchronized_asset(dao, key, **options):
        if key == "parallel-asset":
            library_ready.set()
            assert graph_ready.wait(5), "graph did not reach the project-locked write"
        return read_asset(dao, key, **options)

    def synchronized_graph(service, canvas, value, *, reason):
        graph_ready.set()
        assert library_ready.wait(5), "library did not enter its serialized write"
        return snapshot(service, canvas, value, reason=reason)

    monkeypatch.setattr(CanvasLibraryDAO, "asset", synchronized_asset)
    monkeypatch.setattr(CanvasService, "_snapshot", synchronized_graph)
    with ThreadPoolExecutor(max_workers=2) as pool:
        library = pool.submit(
            owner.put,
            ASSETS + "/parallel-asset",
            json={
                "asset": {
                    "id": "parallel-asset",
                    "kind": "text",
                    "title": "并发素材",
                    "metadata": {"canvasId": document["id"]},
                    "data": {"content": "保留"},
                }
            },
        )
        assert library_ready.wait(5)
        graph = pool.submit(
            owner.post,
            path + "/commits",
            headers={"Idempotency-Key": "parallel-save"},
            json={"expected_row_version": document["revision"], "source_document": document},
        )
        results = [library.result(timeout=10), graph.result(timeout=10)]
    assert [result.status_code for result in results] == [200, 200], (
        [result.text for result in results],
        database_errors,
    )
