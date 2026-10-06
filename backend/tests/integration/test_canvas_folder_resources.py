"""文件夹封面真实 MinIO 字节及回收保护。"""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.integration.test_canvas_library import ASSETS
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_library_deletion import register
from tests.integration.test_canvas_project_folders import FOLDERS, put_folder
from tests.integration.test_canvas_resources import ROOT, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable explicit isolated MinIO verification",
    ),
]


def test_cover_pins_bytes_replacement_and_delete_release_only_the_old_reference(resource_app):
    owner, _ = account(resource_app, "folder_cover_owner")
    other, _ = account(resource_app, "folder_cover_other")
    first = upload(owner, png()).json()["resource"]
    second = upload(owner, png("blue"), key="cover-second").json()["resource"]
    foreign = upload(other, png("green")).json()["resource"]
    register(owner, "cover-first", first)
    register(owner, "cover-second", second)
    assert put_folder(owner, coverResourceId=first["id"]).status_code == 200
    assert owner.get(f"{ROOT}/{first['id']}/file").content == png()
    rejected = owner.delete(ASSETS + "/cover-first")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_in_use"
    assert put_folder(owner, coverResourceId=foreign["id"]).status_code == 404
    assert other.get(f"{ROOT}/{first['id']}/file").status_code == 404
    assert owner.get(FOLDERS).json()["folders"][0]["coverResourceId"] == first["id"]
    assert put_folder(owner, coverResourceId=second["id"]).status_code == 200
    assert owner.delete(ASSETS + "/cover-first").status_code == 200
    assert owner.delete(ASSETS + "/cover-second").status_code == 409
    assert owner.delete(FOLDERS + "/folder-one").status_code == 200
    assert owner.delete(ASSETS + "/cover-second").status_code == 200


def test_cover_replacement_races_resource_delete_without_dangling_cover(
    resource_app, database_errors
):
    owner, _ = account(resource_app, "folder_cover_race")
    resource = upload(owner, png()).json()["resource"]
    register(owner, "racing-cover", resource)
    assert put_folder(owner).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        save = pool.submit(put_folder, owner, coverResourceId=resource["id"])
        removal = pool.submit(owner.delete, ASSETS + "/racing-cover")
        saved, removed = save.result(timeout=15), removal.result(timeout=15)
    assert (saved.status_code, removed.status_code) in {(200, 409), (404, 200)}, (
        saved.text,
        removed.text,
        database_errors,
    )
    folder = owner.get(FOLDERS).json()["folders"][0]
    if saved.status_code == 200:
        assert folder["coverResourceId"] == resource["id"]
        assert owner.get(f"{ROOT}/{resource['id']}/file").content == png()
    else:
        assert not folder.get("coverResourceId")
    assert database_errors == []
