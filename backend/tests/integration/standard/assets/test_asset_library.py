import threading
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AssetImageCandidate, MediaFile
from short_drama.service.creation.assets.images.image import AssetImageService
from short_drama.service.creation.assets.management.library import AssetLibraryService
from short_drama.service.creation.storyboard.management.script import ShotScriptService
from short_drama.service.creation.storyboard.references.asset import ShotAssetService
from short_drama.service.projects.management.project import ProjectService
from tests.support.integration.identity import actor as fixture_actor

pytestmark = pytest.mark.integration


def png_bytes():
    output = BytesIO()
    Image.new("RGB", (3, 2), "red").save(output, format="PNG")
    return output.getvalue()


def test_library_idempotency_sharing_boundaries_and_in_use_removal(db_session):
    projects = ProjectService(db_session)
    first_project = projects.create({"name": "first"})
    other_project = projects.create({"name": "other"})
    libraries = AssetLibraryService(db_session)
    item, created = libraries.create(
        "project",
        first_project.id,
        first_project.id,
        {"kind": "prop", "name": "umbrella", "prompt": "red umbrella"},
        "asset-key",
    )
    assert created is True
    replay, created = libraries.create(
        "project",
        first_project.id,
        first_project.id,
        {"kind": "prop", "name": "umbrella", "prompt": "red umbrella"},
        "asset-key",
    )
    assert (created, replay.id) == (False, item.id)
    with pytest.raises(WorkflowError) as error:
        libraries.create(
            "project",
            first_project.id,
            first_project.id,
            {"kind": "prop", "name": "changed"},
            "asset-key",
        )
    assert error.value.code == "idempotency_conflict"
    assert libraries.list("project", other_project.id, other_project.id)["total"] == 0

    shot = ShotScriptService(db_session).create({"project_id": first_project.id, "position": 1})
    ShotAssetService(db_session).create(
        {"shot_id": shot.id, "project_id": first_project.id, "asset_id": item.id}
    )
    with pytest.raises(WorkflowError) as error:
        libraries.unlink("project", first_project.id, first_project.id, item.id, item.row_version)
    assert error.value.code == "asset_in_use"
    assert error.value.details == {"references": [str(shot.id)]}


def test_deleted_asset_creation_key_remains_reserved_for_the_owner(db_session):
    project = ProjectService(db_session).create({"name": "replay"})
    service = AssetLibraryService(db_session)
    payload = {"kind": "prop", "name": "umbrella"}
    asset, _ = service.create("project", project.id, project.id, payload, "same-key")
    service.unlink("project", project.id, project.id, asset.id, asset.row_version)
    with pytest.raises(WorkflowError) as removed:
        service.create("project", project.id, project.id, payload, "same-key")
    assert removed.value.code == "asset_creation_removed"
    with pytest.raises(WorkflowError) as changed:
        service.create(
            "project", project.id, project.id, {**payload, "name": "changed"}, "same-key"
        )
    assert changed.value.code == "idempotency_conflict"
    replacement, created = service.create("project", project.id, project.id, payload, "new-key")
    assert created and replacement.id != asset.id


def test_concurrent_identical_upload_keeps_one_candidate_and_compensates_loser(
    mysql_engine, db_session
):
    project = ProjectService(db_session).create({"name": "upload"})
    item, _ = AssetLibraryService(db_session).create(
        "project",
        project.id,
        project.id,
        {"kind": "prop", "name": "umbrella", "prompt": "red umbrella"},
        "upload-key",
    )
    data = png_bytes()
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    state = {"next": 0, "deleted": []}

    class Storage:
        def upload(self, _stream, *, length, content_type):
            with lock:
                state["next"] += 1
                number = state["next"]
            barrier.wait(timeout=10)
            return SimpleNamespace(
                storage_locator=f"minio://image/concurrent-{number}.png",
                size=length,
                content_type=content_type,
                version_id=f"v{number}",
            )

        def delete(self, locator, *, version_id=None):
            with lock:
                state["deleted"].append((locator, version_id))

        def download_url(self, locator):
            return f"https://signed.test/{locator.rsplit('/', 1)[-1]}"

    results = []
    errors = []

    def upload():
        try:
            with Session(
                mysql_engine,
                expire_on_commit=False,
                autoflush=False,
                info={"actor": fixture_actor()},
            ) as session:
                result, created = AssetImageService(session, SimpleNamespace(), Storage()).upload(
                    item.id, BytesIO(data), len(data), "same.png", "image/png"
                )
                with lock:
                    results.append((result.id, created))
        except Exception as error:
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=upload) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors
    assert len(results) == 2
    assert {created for _, created in results} == {False, True}
    assert len({identifier for identifier, _ in results}) == 1
    assert len(state["deleted"]) == 1
    with Session(mysql_engine) as session:
        assert session.scalar(select(func.count()).select_from(AssetImageCandidate)) == 1
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 1
