import pytest
from generation_fixtures import generation_session
from pydantic import ValidationError
from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Asset
from short_drama.schemas.asset import AssetCreate, AssetPatch
from short_drama.schemas.asset_library import AssetLibraryCreate
from short_drama.service.asset_library_service import AssetLibraryService, creation_fingerprint
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService


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


def test_asset_patch_requires_a_version_and_rejects_kind_or_empty_patch():
    assert AssetPatch(row_version="3", name="新名称").row_version == 3
    for payload in (
        {"name": "new"},
        {"row_version": "3"},
        {"row_version": "3", "kind": "prop"},
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
