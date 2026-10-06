"""副本保留源素材身份，且跨项目绑定继续遵守素材替换与删除规则。"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from short_drama.core.exceptions import NotFound
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.domain import CanvasLibraryAsset, CanvasLibraryAssetReference
from short_drama.service.base import BaseService
from short_drama.service.canvas_library_service import CanvasLibraryService
from short_drama.service.canvas_service import CanvasService
from short_drama.utils.snowflake import next_id
from tests.integration.test_canvas_library_deletion import ASSETS, commit, image_node, register
from tests.integration.test_canvas_resource_copy import copy_resource
from tests.integration.test_canvas_resources import ROOT, canvas, media_asset_data, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated copy binding MySQL/MinIO verification",
    ),
]


@pytest.mark.parametrize("binding_kind", ["node", "timeline"])
@pytest.mark.parametrize("original_scope", ["personal", "project"])
def test_copy_keeps_one_library_asset_and_guards_replacement_and_deletion(
    resource_app, binding_kind, original_scope
):
    owner, user = account(resource_app, "bound_copy_owner")
    member, member_user = account(resource_app, "bound_copy_member")
    project, path, document = canvas(owner, "bound-copy-target")
    join(resource_app, owner, member, project["id"], member_user["id"])
    source_key = None
    if original_scope == "project":
        _, _, source_document = canvas(owner, "bound-copy-source")
        source_key = source_document["id"]
    original = upload(owner, png(), source=source_key).json()["resource"]
    replacement = upload(owner, png("blue"), key="replacement", source=source_key).json()[
        "resource"
    ]
    register(owner, "single-asset", original)
    copied = copy_resource(owner, original, document["id"]).json()["resource"]
    if binding_kind == "node":
        node = image_node(copied)
        node["metadata"]["assetId"] = "single-asset"
        document["nodes"] = [node]
    else:
        document["timeline"] = {
            "clips": [
                {
                    "id": "stable-clip",
                    "directMedia": {
                        "kind": "image",
                        "assetId": "single-asset",
                        "storageKey": "resource:" + copied["id"],
                    },
                }
            ]
        }
    commit(owner, path, document, "bind-copy")
    aliases = {copied["id"]: [original["id"]]}
    author_document = owner.get(path + "/my-document").json()
    assert author_document["resource_aliases"] == aliases
    assert "resource_aliases" not in author_document["source_document"]
    assert owner.get(path).json()["resource_aliases"] == {}
    assert member.get(path + "/my-document").json()["resource_aliases"] == {}
    with resource_app[1]() as session:
        assets = list(session.scalars(select(CanvasLibraryAsset)))
        assert len(assets) == 1
        identifiers = set(session.scalars(select(CanvasLibraryAssetReference.media_id)))
        assert identifiers == {int(original["id"]), int(copied["id"])}
    asset_path = ASSETS + "/single-asset"
    asset = owner.get(asset_path).json()["asset"]
    assert asset["data"]["storageKey"] == "resource:" + original["id"]
    asset["title"] = "只改标题，保留原素材"
    assert owner.put(asset_path, json={"asset": asset}).status_code == 200
    asset["data"] = media_asset_data(replacement)
    rejected = owner.put(asset_path, json={"asset": asset})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_resource_conflict"
    rejected = owner.delete(asset_path)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_in_use"
    assert owner.get(f"{ROOT}/{original['id']}/file").content == png()
    assert member.get(asset_path).status_code == 404
    assert member.get(f"{ROOT}/{copied['id']}/file").content == png()
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(member_user["id"]))
        assert list(session.scalars(select(CanvasLibraryAssetReference))) == []
    # Detaching keeps the original asset identity; replacing its file is now allowed.
    document["nodes"] = []
    document.pop("timeline", None)
    commit(owner, path, document, "detach-copy")
    allowed = owner.put(asset_path, json={"asset": asset})
    assert allowed.status_code == 200, allowed.text
    assert owner.get(asset_path).json()["asset"]["data"]["storageKey"] == (
        "resource:" + replacement["id"]
    )


def test_copy_cannot_be_bound_to_an_unrelated_original_asset(resource_app):
    owner, _ = account(resource_app, "unrelated_copy")
    _, path, document = canvas(owner, "unrelated-copy-target")
    original = upload(owner, png()).json()["resource"]
    unrelated = upload(owner, png("blue"), key="unrelated").json()["resource"]
    register(owner, "unrelated-asset", unrelated)
    copied = copy_resource(owner, original, document["id"]).json()["resource"]
    node = image_node(copied)
    node["metadata"]["assetId"] = "unrelated-asset"
    document["nodes"] = [node]
    rejected = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "wrong-binding"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_resource_conflict"
    assert owner.get(path + "/my-document").json()["source_document"]["nodes"] == []


def test_direct_cross_project_reference_requires_owned_copy_provenance(resource_app):
    owner, user = account(resource_app, "copy_reference_owner")
    _, _, document = canvas(owner, "reference-target")
    original = upload(owner, png()).json()["resource"]
    unrelated = upload(owner, png("blue"), key="foreign-target", source=document["id"]).json()[
        "resource"
    ]
    register(owner, "reference-asset", original)
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(user["id"]))
        asset = session.scalar(select(CanvasLibraryAsset))
        session.add(
            CanvasLibraryAssetReference(
                id=next_id(),
                library_asset_id=asset.id,
                media_id=int(unrelated["id"]),
                binary_id=None,
            )
        )
        with pytest.raises(NotFound, match="scope"):
            session.flush()
        session.rollback()


def test_copy_of_copy_keeps_private_lineage_after_intermediate_project_revocation(resource_app):
    owner, owner_user = account(resource_app, "lineage_project_owner")
    member, member_user = account(resource_app, "lineage_copy_author")
    intermediate, _, middle_doc = canvas(owner, "lineage-middle")
    join(resource_app, owner, member, intermediate["id"], member_user["id"])
    target, target_path, target_doc = canvas(member, "lineage-final")
    join(resource_app, member, owner, target["id"], owner_user["id"])
    original = upload(member, png()).json()["resource"]
    register(member, "lineage-asset", original)
    middle = copy_resource(member, original, middle_doc["id"], key="middle").json()["resource"]
    final = copy_resource(member, middle, target_doc["id"], key="final").json()["resource"]
    revoked = owner.delete(f"/api/v1/projects/{intermediate['id']}/members/{member_user['id']}")
    assert revoked.status_code in {200, 204}, revoked.text
    assert member.get(f"{ROOT}/{middle['id']}/file").status_code == 404
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(member_user["id"]))
        assert CanvasResourceDAO(session).copy_ancestors({int(final["id"])}) == {
            int(final["id"]): {int(middle["id"]), int(original["id"])}
        }
    node = image_node(final)
    node["metadata"]["assetId"] = "lineage-asset"
    target_doc["nodes"] = [node]
    commit(member, target_path, target_doc, "attach-final")
    assert member.get(target_path + "/my-document").json()["resource_aliases"] == {
        final["id"]: sorted([middle["id"], original["id"]], key=int)
    }
    assert owner.get(target_path + "/my-document").json()["resource_aliases"] == {}
    assert member.delete(ASSETS + "/lineage-asset").status_code == 409
    assert member.get(f"{ROOT}/{final['id']}/file").content == png()
    assert owner.get(f"{ROOT}/{final['id']}/file").content == png()
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(intermediate["owner_user_id"]))
        assert CanvasResourceDAO(session).copy_ancestors({int(final["id"])}) == {}


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_pending_copy_binding_serializes_library_changes(resource_app, monkeypatch, operation):
    owner, _ = account(resource_app, "binding_serialization")
    _, path, document = canvas(owner, "binding-race")
    original = upload(owner, png()).json()["resource"]
    replacement = upload(owner, png("blue"), key="replacement").json()["resource"]
    register(owner, "serialized-asset", original)
    asset_path = ASSETS + "/serialized-asset"
    asset = owner.get(asset_path).json()["asset"]
    asset["data"] = media_asset_data(replacement)
    copied = copy_resource(owner, original, document["id"]).json()["resource"]
    node = image_node(copied)
    node["metadata"]["assetId"] = "serialized-asset"
    document["nodes"] = [node]
    binding_started, library_started, finish_binding = Event(), Event(), Event()
    bind = CanvasService._bind_library_copies
    mutex = BaseService._global_order_lock

    def wait_before_binding(service, value):
        binding_started.set()
        assert finish_binding.wait(10), "library did not attempt its write"
        return bind(service, value)

    @contextmanager
    def announce_library_lock(service):
        if isinstance(service, CanvasLibraryService):
            library_started.set()
        with mutex(service):
            yield

    monkeypatch.setattr(CanvasService, "_bind_library_copies", wait_before_binding)
    monkeypatch.setattr(BaseService, "_global_order_lock", announce_library_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(commit, owner, path, document, "race-attach-copy")
        assert binding_started.wait(10)
        changing = (
            pool.submit(owner.delete, asset_path)
            if operation == "delete"
            else pool.submit(owner.put, asset_path, json={"asset": asset})
        )
        try:
            assert library_started.wait(10)
        finally:
            finish_binding.set()
        saving.result(timeout=10)
        rejected = changing.result(timeout=10)
    assert rejected.status_code == 409, rejected.text
    assert owner.get(asset_path).json()["asset"]["data"]["storageKey"] == (
        "resource:" + original["id"]
    )
    assert owner.get(f"{ROOT}/{original['id']}/file").content == png()


def test_copy_binding_in_history_blocks_deletion_without_publishing_source(resource_app):
    owner, _ = account(resource_app, "binding_history")
    member, member_user = account(resource_app, "binding_history_member")
    project, path, document = canvas(owner, "binding-history")
    join(resource_app, owner, member, project["id"], member_user["id"])
    original = upload(owner, png()).json()["resource"]
    register(owner, "historical-asset", original)
    copied = copy_resource(owner, original, document["id"]).json()["resource"]
    node = image_node(copied)
    node["metadata"]["assetId"] = "historical-asset"
    document["nodes"] = [node]
    saved = commit(owner, path, document, "attach-for-history")
    empty_revision = owner.get(path + "/revisions").json()["items"][0]
    restored = owner.post(
        path + f"/revisions/{empty_revision['id']}/restore",
        headers={"Idempotency-Key": "restore-before-copy"},
        json={"expected_row_version": saved["row_version"]},
    )
    assert restored.status_code == 200, restored.text
    revisions = owner.get(path + "/revisions").json()["items"]
    copied_revision = next(
        item for item in revisions if item["row_version"] == saved["row_version"]
    )
    revision_path = path + f"/revisions/{copied_revision['id']}"
    author_revision = owner.get(revision_path).json()
    assert author_revision["resource_aliases"] == {copied["id"]: [original["id"]]}
    assert "resource_aliases" not in author_revision["source_document"]
    assert member.get(revision_path).json()["resource_aliases"] == {}
    assert owner.get(path + "/my-document").json()["resource_aliases"] == {}
    rejected = owner.delete(ASSETS + "/historical-asset")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_history_referenced"
    assert owner.get(ASSETS + "/historical-asset").status_code == 200
    assert owner.get(f"{ROOT}/{original['id']}/file").content == png()
    assert owner.get(f"{ROOT}/{copied['id']}/file").content == png()


def test_workspace_batches_copy_aliases_and_returns_only_each_documents_resources(resource_app):
    owner, _ = account(resource_app, "copy_alias_page")
    member, member_user = account(resource_app, "copy_alias_page_member")
    original = upload(owner, png()).json()["resource"]
    register(owner, "page-asset", original)
    expected = {}
    for index in range(3):
        project, path, document = canvas(owner, f"alias-page-{index}")
        join(resource_app, owner, member, project["id"], member_user["id"])
        copied = copy_resource(owner, original, document["id"], key=f"page-copy-{index}").json()[
            "resource"
        ]
        document["nodes"] = [image_node(copied, assetId="page-asset")]
        commit(owner, path, document, f"page-commit-{index}")
        expected[document["id"]] = {copied["id"]: [original["id"]]}
    statements = []

    def collect(_connection, _cursor, statement, _parameters, _context, _many):
        if "from canvas_resource_copy_sources" in statement.lower():
            statements.append(statement)

    with resource_app[1]() as session:
        engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", collect)
    try:
        page = owner.get("/api/v1/canvas-workspace", params={"include_documents": "true"})
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert page.status_code == 200, page.text
    assert len(statements) == 1, "copy lineage must be read in a batch, not once per canvas"
    assert len(page.json()["items"]) == 3
    for item in page.json()["items"]:
        assert item["resource_aliases"] == expected[item["source_key"]]
        assert "resource_aliases" not in item["source_document"]
    summary = owner.get("/api/v1/canvas-workspace").json()
    assert all("resource_aliases" not in item for item in summary["items"])
    member_page = member.get(
        "/api/v1/canvas-workspace", params={"include_documents": "true", "page_size": 2}
    ).json()
    assert member_page["has_more"] and len(member_page["items"]) == 2
    assert all(item["resource_aliases"] == {} for item in member_page["items"])
