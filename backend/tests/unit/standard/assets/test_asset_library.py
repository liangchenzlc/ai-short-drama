from io import BytesIO
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Asset, AssetVersion
from short_drama.schemas.creation.assets.management.assets import AssetCreate, AssetPatch
from short_drama.schemas.creation.assets.management.library import AssetLibraryCreate
from short_drama.service.base import utcnow
from short_drama.service.creation.assets.images.image import AssetImageService
from short_drama.service.creation.assets.management.assets import AssetService
from short_drama.service.creation.assets.management.library import (
    AssetLibraryService,
    creation_fingerprint,
)
from short_drama.service.projects.management.project import ProjectService
from tests.support.generation_fixtures import generation_session


def test_asset_create_normalizes_tags_and_enforces_scene_time_scope():
    created = AssetCreate(
        kind="scene",
        name="  雨夜街口  ",
        tags=[" 雨夜 ", "城市", "雨夜"],
        scene_time=" 夜晚 ",
    )
    assert created.name == "雨夜街口"
    assert created.tags == ["雨夜", "城市"]
    assert created.scene_time == "夜晚"

    with pytest.raises(ValidationError):
        AssetCreate(kind="character", name="人物", scene_time="夜晚")


def test_asset_patch_requires_a_version_and_rejects_invalid_kind_or_empty_patch():
    assert AssetPatch(row_version="3", name="新名称").row_version == 3
    assert AssetPatch(row_version="3", kind="prop").kind == "prop"
    for payload in (
        {"name": "new"},
        {"row_version": "3"},
        {"row_version": "3", "kind": "invalid"},
    ):
        with pytest.raises(ValidationError):
            AssetPatch.model_validate(payload)


@pytest.mark.parametrize("field", ["model_id", "media_id", "state", "row_version"])
def test_public_asset_creation_rejects_server_owned_fields_even_when_null(field):
    with pytest.raises(ValidationError):
        AssetLibraryCreate.model_validate({"kind": "prop", "name": "umbrella", field: None})


def test_creation_fingerprint_includes_scope_and_normalized_payload():
    payload = AssetCreate(kind="prop", name=" 伞 ", tags=["雨", "雨"])
    project = creation_fingerprint("project", 7, payload)
    assert project == creation_fingerprint(
        "project", 7, AssetCreate(kind="prop", name="伞", tags=["雨"])
    )
    assert project != creation_fingerprint("project", 8, payload)
    assert project != creation_fingerprint("episode", 7, payload)


def test_asset_fields_reject_retired_shared_confirmation():
    with pytest.raises(ValidationError):
        AssetPatch(row_version=1, prompt="portrait", confirm_shared=True)


def test_project_creation_replay_does_not_restore_removed_asset():
    with generation_session() as session:
        project = ProjectService(session).create({"name": "Work"})
        service = AssetLibraryService(session)
        payload = {"kind": "prop", "name": "umbrella"}
        first, created = service.create("project", project.id, project.id, payload, "first")
        replay, again = service.create("project", project.id, project.id, payload, "first")
        assert created and not again and replay.id == first.id
        second, _ = service.create("project", project.id, project.id, payload, "second")
        assert second.id != first.id
        service.unlink("project", project.id, project.id, first.id, first.row_version)
        with pytest.raises(WorkflowError) as removed:
            service.create("project", project.id, project.id, payload, "first")
        assert removed.value.code == "asset_creation_removed"
        assert [item.id for item in service.list("project", project.id, project.id)["items"]] == [
            second.id
        ]
        with pytest.raises(NotFound):
            service.get(first.id)


@pytest.mark.parametrize("operation", ["list", "candidate", "confirm", "upload", "edit"])
def test_removed_asset_rejects_internal_image_and_edit_operations(operation):
    with generation_session() as session:
        project = ProjectService(session).create({"name": "Work"})
        library = AssetLibraryService(session)
        item, _ = library.create(
            "project", project.id, project.id, {"kind": "prop", "name": "umbrella"}, "removed"
        )
        library.unlink("project", project.id, project.id, item.id, item.row_version)

        def unexpected_upload(*_args, **_kwargs):
            pytest.fail("Removed assets must be rejected before storage I/O")

        images = AssetImageService(
            session, SimpleNamespace(), SimpleNamespace(upload=unexpected_upload)
        )
        with pytest.raises(NotFound):
            if operation == "list":
                images.list(item.id)
            elif operation == "candidate":
                images.add_candidate(item.id, "999")
            elif operation == "confirm":
                images.confirm(
                    item.id,
                    {"row_version": item.row_version, "media_id": "999", "expected_media_id": None},
                )
            elif operation == "upload":
                images.upload(item.id, BytesIO(b""), 0, "image.png")
            else:
                AssetService(session).update(
                    item.id, {"row_version": item.row_version, "name": "new"}
                )


def test_project_patch_keeps_cas_and_immutable_artwork_without_shared_confirmation():
    with generation_session() as session:
        project = ProjectService(session).create({"name": "Work"})
        service = AssetLibraryService(session)
        item, _ = service.create(
            "project", project.id, project.id, {"kind": "character", "name": "Hero"}, "hero"
        )
        changed = service.patch(item.id, {"row_version": "1", "description": "changed"})
        assert (changed.row_version, changed.state, changed.reference_count) == (
            2,
            "unconfirmed",
            0,
        )
        with pytest.raises(WorkflowError) as conflict:
            service.patch(item.id, {"row_version": "1", "description": "stale"})
        assert conflict.value.code == "asset_version_conflict"
        with session.begin():
            history = list(
                session.scalars(
                    select(AssetVersion)
                    .where(AssetVersion.asset_id == int(item.id))
                    .order_by(AssetVersion.version)
                )
            )
            assert [row.version for row in history] == [1, 2]
            assert session.get(Asset, int(item.id)).current_version_id == history[-1].id


def test_unchanged_save_fences_delayed_input_without_artwork_history():
    with generation_session() as session:
        project = ProjectService(session).create({"name": "Work"})
        service = AssetLibraryService(session)
        item, _ = service.create(
            "project", project.id, project.id, {"kind": "character", "name": "Hero"}, "hero"
        )
        with session.begin():
            stored = session.get(Asset, int(item.id))
            pointer = stored.current_version_id
        saved = service.patch(item.id, {"row_version": "1", "name": "Hero"})
        assert saved.row_version == 2
        with session.begin():
            assert session.get(Asset, int(item.id)).current_version_id == pointer
            assert len(list(session.scalars(select(AssetVersion)))) == 1


def test_only_active_shot_references_block_removal_and_pages_batch_counts():
    from short_drama.domain import ShotScript
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.creation.storyboard.references.asset import ShotAssetService

    with generation_session() as session:
        project = ProjectService(session).create({"name": "Work"})
        other = ProjectService(session).create({"name": "Other"})
        service = AssetLibraryService(session)
        items = [
            service.create(
                "project",
                project.id,
                project.id,
                {"kind": "prop", "name": f"prop-{index}"},
                f"asset-{index}",
            )[0]
            for index in range(3)
        ]
        shot = ShotScriptService(session).create({"project_id": project.id, "position": 1})
        ShotAssetService(session).create(
            {"project_id": project.id, "shot_id": shot.id, "asset_id": items[1].id}
        )
        statements = []

        def count(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            page = service.list("project", project.id, project.id)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert [item.id for item in page["items"]] == [item.id for item in items]
        assert [item.reference_count for item in page["items"]] == [0, 1, 0]
        assert len(statements) <= 5
        with pytest.raises(WorkflowError) as referenced:
            service.unlink("project", project.id, project.id, items[1].id, "1")
        assert referenced.value.code == "asset_in_use"
        assert service.list("project", other.id, other.id)["total"] == 0
        with session.begin():
            session.get(ShotScript, int(shot.id)).deleted_at = utcnow()
        service.unlink("project", project.id, project.id, items[1].id, "1")
        assert service.list("project", project.id, project.id)["total"] == 2
