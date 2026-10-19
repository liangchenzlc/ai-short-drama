import pytest
from generation_fixtures import generation_session
from pydantic import ValidationError
from sqlalchemy import event, select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Asset, AssetVersion, MediaFile
from short_drama.schemas.asset import AssetCreate, AssetPatch
from short_drama.schemas.asset_library import AssetLibraryCreate
from short_drama.service.asset_library_service import AssetLibraryService, creation_fingerprint
from short_drama.service.asset_version_service import AssetVersionService
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService


def test_project_asset_list_keeps_query_count_bounded_as_page_grows():
    with generation_session() as session:
        project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
        service = AssetLibraryService(session)
        ids = [
            service.create(
                "project",
                project.id,
                project.id,
                {"kind": "prop", "name": f"prop-{index}"},
                f"asset-{index}",
            )[0].id
            for index in range(3)
        ]
        episode = EpisodeService(session).create(
            {"project_id": project.id, "position": 1, "title": "E", "aspect": "16:9"}
        )
        service.link("episode", episode.id, project.id, ids[1])
        statements = []

        def count_query(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", count_query)
        try:
            page = service.list("project", project.id, project.id)
        finally:
            event.remove(engine, "before_cursor_execute", count_query)

        assert [item.id for item in page["items"]] == ids
        assert [item.reference_count for item in page["items"]] == [1, 2, 1]
        assert len(statements) <= 7


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


def test_asset_read_model_exposes_shared_versioned_state():
    patch = AssetPatch(row_version=1, prompt="portrait", confirm_shared=True)
    assert patch.model_dump(exclude_unset=True) == {
        "row_version": 1,
        "prompt": "portrait",
        "confirm_shared": True,
    }


def test_project_creation_is_idempotent_without_restoring_removed_link_and_keeps_same_names():
    with generation_session() as session:
        project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
        service = AssetLibraryService(session)
        payload = {"kind": "prop", "name": "umbrella", "prompt": "red umbrella"}
        first, created = service.create("project", project.id, project.id, payload, "key-1")
        assert created is True
        replay, created = service.create("project", project.id, project.id, payload, "key-1")
        assert (created, replay.id, replay.link_id) == (False, first.id, first.link_id)
        second, created = service.create("project", project.id, project.id, payload, "key-2")
        assert created is True
        assert second.id != first.id

        service.unlink("project", project.id, project.id, first.id, first.row_version)
        replay, created = service.create("project", project.id, project.id, payload, "key-1")
        assert created is False
        assert replay.link_id is replay.position is None
        assert session.scalar(select(Asset).where(Asset.id == first.id)) is not None


def test_shared_patch_requires_confirmation_and_advances_one_asset_version():
    with generation_session() as session:
        project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
        episode = EpisodeService(session).create(
            {"project_id": project.id, "position": 1, "title": "E", "aspect": "16:9"}
        )
        service = AssetLibraryService(session)
        item, _ = service.create(
            "project",
            project.id,
            project.id,
            {"kind": "character", "name": "Hero", "description": "lead"},
            "shared-1",
        )
        service.link("episode", episode.id, project.id, item.id)
        with pytest.raises(WorkflowError) as error:
            service.patch(item.id, {"row_version": "1", "description": "changed"})
        assert error.value.code == "shared_asset_confirmation_required"

        changed = service.patch(
            item.id,
            {"row_version": "1", "description": "changed", "confirm_shared": True},
        )
        assert (changed.row_version, changed.state, changed.reference_count) == (
            2,
            "unconfirmed",
            2,
        )
        with pytest.raises(WorkflowError) as error:
            service.patch(item.id, {"row_version": "1", "description": "stale"})
        assert error.value.code == "asset_version_conflict"
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


def test_unchanged_shared_asset_save_fences_delayed_edit_without_new_artwork_or_confirmation():
    with generation_session() as session:
        project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
        episode = EpisodeService(session).create(
            {"project_id": project.id, "position": 1, "title": "E", "aspect": "16:9"}
        )
        service = AssetLibraryService(session)
        asset, _ = service.create(
            "project", project.id, project.id, {"kind": "character", "name": "Hero"}, "fence"
        )
        service.link("episode", episode.id, project.id, asset.id)
        with session.begin():
            now = utcnow()
            row = session.get(Asset, int(asset.id))
            image = MediaFile(
                id=1,
                project_id=row.project_id,
                scope_user_id=row.scope_user_id,
                format_code="image/png",
                storage_locator="minio://images/fence.png",
                original_name="fence.png",
                created_at=now,
                updated_at=now,
            )
            session.add(image)
            session.flush()
            row.media_id = image.id
            row.state = "confirmed"
            AssetVersionService(session).append_locked(row)
            artwork_version = row.current_version_id
            history = list(session.scalars(select(AssetVersion.id)))
        saved = service.patch(asset.id, {"row_version": "1", "name": "Hero"})
        assert saved.row_version == 2
        assert saved.state == "confirmed"
        assert saved.reference_count == 2
        with pytest.raises(WorkflowError) as conflict:
            service.patch(
                asset.id,
                {"row_version": "1", "name": "obsolete", "confirm_shared": True},
            )
        assert conflict.value.code == "asset_version_conflict"
        with session.begin():
            row = session.get(Asset, int(asset.id))
            assert row.name == "Hero"
            assert row.current_version_id == artwork_version
            assert list(session.scalars(select(AssetVersion.id))) == history
