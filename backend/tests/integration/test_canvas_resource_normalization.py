"""保存前资源归一化使用真实 MySQL/MinIO，重试不产生重复文件。"""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import StorageUnavailable
from short_drama.domain import CanvasResourceUpload, MediaFile
from tests.integration.test_canvas_library_deletion import ASSETS, commit, image_node, register
from tests.integration.test_canvas_resources import ROOT, canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated resource normalization MySQL/MinIO verification",
    ),
]


def normalize(client, document, *resources):
    return client.post(
        ROOT + "/normalize",
        json={"canvas_key": document["id"], "resource_ids": [item["id"] for item in resources]},
    )


def test_normalization_reuses_same_project_resources_and_copies_foreign_files_once(resource_app):
    client, _ = account(resource_app, "normalize_owner")
    _, target_path, target = canvas(client, "normalize-target")
    _, _, foreign = canvas(client, "normalize-origin")
    direct = upload(client, png(), source=target["id"], key="direct").json()["resource"]
    personal = upload(client, png("blue"), key="personal").json()["resource"]
    source = upload(client, png("green"), source=foreign["id"], key="foreign").json()["resource"]
    register(client, "same-personal-asset", personal)
    response = normalize(client, target, direct, personal, source, personal)
    assert response.status_code == 200, response.text
    result = response.json()
    mapping = result["resource_map"]
    assert set(mapping) == {direct["id"], personal["id"], source["id"]}
    assert mapping[direct["id"]] == direct["id"]
    for original in (personal, source):
        assert mapping[original["id"]] != original["id"]
        assert result["resource_aliases"][mapping[original["id"]]] == [original["id"]]
        assert (
            client.get(f"{ROOT}/{mapping[original['id']]}/file").content
            == client.get(f"{ROOT}/{original['id']}/file").content
        )
    repeated = normalize(client, target, source, personal, direct).json()
    assert repeated == result
    assert normalize(client, target, personal).json()["resource_map"] == {
        personal["id"]: mapping[personal["id"]]
    }
    with resource_app[1]() as session:
        copies = list(
            session.scalars(select(CanvasResourceUpload).where(CanvasResourceUpload.mode == "copy"))
        )
        assert len(copies) == 2 and all(item.status == "ready" for item in copies)
    target["nodes"] = [image_node({"id": mapping[personal["id"]]}, assetId="same-personal-asset")]
    commit(client, target_path, target, "normalized-commit")
    assert client.get(ASSETS + "/same-personal-asset").json()["asset"]["data"]["storageKey"] == (
        "resource:" + personal["id"]
    )
    assert len(client.get(ASSETS).json()["assets"]) == 1


def test_normalization_recovers_completed_copy_after_origin_revocation_but_not_target_revocation(
    resource_app,
):
    owner, _ = account(resource_app, "normalize_project_owner")
    author, author_user = account(resource_app, "normalize_author")
    source_project, source_path, source_doc = canvas(owner, "normalize-shared-source")
    target_project, _, target_doc = canvas(owner, "normalize-shared-target")
    for project in (source_project, target_project):
        join(resource_app, owner, author, project["id"], author_user["id"])
    source = upload(owner, png(), source=source_doc["id"]).json()["resource"]
    source_doc["nodes"] = [image_node(source)]
    commit(owner, source_path, source_doc, "publish-normalize-source")
    first = normalize(author, target_doc, source)
    assert first.status_code == 200, first.text
    assert owner.delete(
        f"/api/v1/projects/{source_project['id']}/members/{author_user['id']}"
    ).status_code in {200, 204}
    assert normalize(author, target_doc, source).json() == first.json()
    assert owner.delete(
        f"/api/v1/projects/{target_project['id']}/members/{author_user['id']}"
    ).status_code in {200, 204}
    assert normalize(author, target_doc, source).status_code == 404


def test_normalization_partial_storage_failure_resumes_same_individual_copy_identities(
    resource_app, monkeypatch
):
    client, _ = account(resource_app, "normalize_resume")
    _, path, target = canvas(client, "normalize-resume")
    first = upload(client, png(), key="first").json()["resource"]
    second = upload(client, png("blue"), key="second").json()["resource"]
    storage = resource_app[0].state.storage
    original_copy = storage.copy
    copied_origins = []
    fail = True

    def uncertain_copy(bucket, source, destination):
        copied_origins.append(source)
        stored = original_copy(bucket, source, destination)
        if fail and source.endswith("/" + second["id"]):
            raise StorageUnavailable("injected lost storage response")
        return stored

    monkeypatch.setattr(storage, "copy", uncertain_copy)
    failed = normalize(client, target, first, second)
    assert failed.status_code == 503, failed.text
    with resource_app[1]() as session:
        pending = list(
            session.scalars(select(CanvasResourceUpload).where(CanvasResourceUpload.mode == "copy"))
        )
        assert len(pending) == 2
        identities = {str(item.reserved_resource_id) for item in pending}
    assert client.get(path + "/my-document").json()["source_document"]["nodes"] == []
    fail = False
    retried = normalize(client, target, second, first)
    assert retried.status_code == 200, retried.text
    assert set(retried.json()["resource_map"].values()) == identities
    assert sum(value.endswith("/" + first["id"]) for value in copied_origins) == 1
    with resource_app[1]() as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(CanvasResourceUpload).where(CanvasResourceUpload.mode == "copy")
                    )
                )
            )
            == 2
        )


def test_normalization_preserves_deleted_copy_tombstone(resource_app):
    client, _ = account(resource_app, "normalize_deleted")
    _, _, target = canvas(client, "normalize-deleted")
    source = upload(client, png()).json()["resource"]
    first = normalize(client, target, source)
    assert first.status_code == 200, first.text
    copied_id = first.json()["resource_map"][source["id"]]
    copied = client.get(f"{ROOT}/{copied_id}").json()["resource"]
    register(client, "delete-auto-copy", copied)
    deleted = client.delete(ASSETS + "/delete-auto-copy")
    assert deleted.status_code == 200, deleted.text
    rejected = normalize(client, target, source)
    assert rejected.status_code == 410, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_resource_deleted"
    with resource_app[1]() as session:
        assert list(session.scalars(select(MediaFile.id))) == [int(source["id"])]


def test_normalization_checks_access_and_validates_batch_before_copying(resource_app):
    owner, _ = account(resource_app, "normalize_private_owner")
    other, other_user = account(resource_app, "normalize_other")
    project, _, target = canvas(owner, "normalize-private")
    source = upload(owner, png()).json()["resource"]
    assert normalize(other, target, source).status_code == 404
    join(resource_app, owner, other, project["id"], other_user["id"])
    assert normalize(other, target, source).status_code == 404
    for ids in ([], [source["id"]] * 201, ["18446744073709551616"], [True]):
        rejected = owner.post(
            ROOT + "/normalize", json={"canvas_key": target["id"], "resource_ids": ids}
        )
        assert rejected.status_code == 422, rejected.text
    with resource_app[1]() as session:
        assert (
            list(
                session.scalars(
                    select(CanvasResourceUpload).where(CanvasResourceUpload.mode == "copy")
                )
            )
            == []
        )


def test_concurrent_normalization_returns_one_copy_for_each_source(resource_app):
    client, _ = account(resource_app, "normalize_concurrent")
    _, _, target = canvas(client, "normalize-concurrent")
    source = upload(client, png()).json()["resource"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: normalize(client, target, source), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    with resource_app[1]() as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(CanvasResourceUpload).where(CanvasResourceUpload.mode == "copy")
                    )
                )
            )
            == 1
        )
