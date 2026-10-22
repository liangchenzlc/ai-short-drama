import pytest
from pydantic import ValidationError
from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import Asset, CharacterVoice, MediaFile, ShotAsset, ShotDialogue
from short_drama.schemas.project_storyboard import StoryboardCreate
from short_drama.schemas.project_writing import ScriptSave
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.project_storyboard_service import ProjectStoryboardService
from tests.support.generation_fixtures import generation_session
from tests.unit.standard.storyboard.test_project_storyboard import setup_storyboard
from tests.unit.standard.writing.test_project_writing import setup


def test_title_and_shot_size_survive_versioned_create_update_and_read():
    with generation_session() as session:
        project, episode = setup_storyboard(session)
        service = ProjectStoryboardService(session)
        created = service.create(
            episode,
            {"storyboard_version": "1", "title": "雨夜初见", "shot_size": "wide_shot"},
            "named-shot",
        )["shot"]
        assert created["title"] == "雨夜初见"
        assert created["shot_size"] == "wide_shot"
        updated = service.update(
            episode,
            created["id"],
            {
                "row_version": created["row_version"],
                "title": "归来",
                "shot_size": None,
            },
        )["shot"]
        assert service.get(episode, created["id"])["shot"] == updated
        assert updated["title"] == "归来" and updated["shot_size"] is None
        assert updated["context_hash"] == created["context_hash"]
        assert updated["duration_ms"] == 3000


@pytest.mark.parametrize(
    "fields", [{"title": "x" * 256}, {"title": None}, {"shot_size": "arbitrary_camera_text"}]
)
def test_shot_metadata_rejects_oversize_null_title_and_unknown_size(fields):
    with pytest.raises(ValidationError):
        StoryboardCreate.model_validate({"storyboard_version": "1", **fields})


def test_format_only_save_is_versioned_and_old_text_clients_clear_formats():
    with generation_session() as session:
        episode, service = setup(session)
        initial = service.save_script(
            episode,
            {
                "script_id": None,
                "content": "中文\n\n",
                "content_version": "1",
            },
        )
        formatted = {
            "ops": [{"insert": "中文", "attributes": {"bold": True}}, {"insert": "\n\n\n"}]
        }
        saved = service.save_script(
            episode,
            {
                "script_id": initial["script"]["id"],
                "content": "中文\n\n",
                "content_version": initial["content_version"],
                "editor_document": formatted,
            },
        )
        assert saved["content_version"] == "3"
        assert service.get(episode)["editing_script"]["editor_document"] == formatted
        with pytest.raises(Conflict):
            service.save_script(
                episode,
                {
                    "script_id": initial["script"]["id"],
                    "content": "中文\n\n",
                    "content_version": "2",
                    "editor_document": formatted,
                },
            )
        old_client = service.save_script(
            episode,
            {
                "script_id": initial["script"]["id"],
                "content": "中文\n\n",
                "content_version": "3",
            },
        )
        assert old_client["script"]["editor_document"] is None
        assert old_client["content_version"] == "4"


@pytest.mark.parametrize(
    "document",
    [
        {"ops": [{"insert": {"image": "https://invalid.test/image"}}]},
        {"ops": [{"insert": "中文\n", "attributes": {"color": "red"}}]},
        {"ops": [{"insert": "中文\n", "attributes": {"header": 7}}]},
        {"ops": [{"insert": "other\n"}]},
        {"ops": [{"insert": "中文"}]},
    ],
)
def test_delta_rejects_embeds_unknown_formats_and_mismatched_text(document):
    with pytest.raises(ValidationError):
        ScriptSave.model_validate(
            {
                "script_id": None,
                "content": "中文",
                "content_version": "1",
                "editor_document": document,
            }
        )


def test_scene_type_change_preserves_shot_links_and_requires_shared_confirmation():
    with generation_session() as session:
        project, episode = setup_storyboard(session)
        assets = AssetLibraryService(session)
        asset, _ = assets.create(
            "project",
            episode,
            project,
            {"kind": "scene", "name": "雨夜街口", "scene_time": "夜晚"},
            "kind-change",
        )
        with session.begin():
            session.add(
                MediaFile(
                    id=101,
                    project_id=int(project),
                    format_code="image/png",
                    storage_locator="minio://i/s.png",
                )
            )
            stored = session.get(Asset, asset.id)
            stored.media_id, stored.state = 101, "confirmed"
        storyboard = ProjectStoryboardService(session)
        shot = storyboard.create(
            episode,
            {"storyboard_version": "1", "script": "街口。", "asset_ids": [str(asset.id)]},
            "kind-shot",
        )["shot"]
        with pytest.raises(WorkflowError) as incompatible:
            assets.patch(asset.id, {"row_version": "1", "kind": "prop"})
        assert incompatible.value.code == "invalid_asset"
        patch = {"row_version": "1", "kind": "prop", "scene_time": ""}
        changed = assets.patch(asset.id, patch)
        assert (changed.kind, changed.row_version, changed.state) == ("prop", 2, "unconfirmed")
        assert changed.reference_count == 1
        updated = storyboard.get(episode, shot["id"])["shot"]
        assert updated["asset_ids"] == [str(asset.id)]
        assert updated["context_hash"] != shot["context_hash"]
        with session.begin():
            assert session.scalar(select(ShotAsset.id).where(ShotAsset.asset_id == asset.id))
        with pytest.raises(WorkflowError) as stale:
            assets.patch(asset.id, {**patch})
        assert stale.value.code == "asset_version_conflict"


def test_prop_can_become_character_but_character_history_cannot_be_discarded():
    with generation_session() as session:
        project, episode = setup_storyboard(session)
        assets = AssetLibraryService(session)
        asset, _ = assets.create(
            "project", episode, project, {"kind": "prop", "name": "木偶"}, "puppet"
        )
        changed = assets.patch(asset.id, {"row_version": "1", "kind": "character"})
        assert changed.kind == "character" and changed.row_version == 2
        with session.begin():
            pointer = session.get(Asset, asset.id).current_version_id
            assert pointer is not None
        with pytest.raises(WorkflowError) as history:
            assets.patch(asset.id, {"row_version": "2", "kind": "scene"})
        assert history.value.code == "asset_kind_incompatible"
        with session.begin():
            assert session.get(Asset, asset.id).current_version_id == pointer
        session.info["actor"] = ActorContext(2, "other", "other@example.test", True, 1, "", "test")
        with pytest.raises(NotFound):
            assets.patch(asset.id, {"row_version": "2", "kind": "prop"})


@pytest.mark.parametrize("dependency", ["voice", "dialogue"])
def test_legacy_character_voice_and_dialogue_dependencies_block_type_change(dependency):
    with generation_session() as session:
        project, episode = setup_storyboard(session)
        # 旧角色尚未回填不可变版本，仍必须保留原声音或对白依赖。
        with session.begin():
            asset = Asset(
                id=99, project_id=int(project), position=1, kind="character", name="旧角色"
            )
            session.add(asset)
            session.flush()
        assets = AssetLibraryService(session)
        shot = ProjectStoryboardService(session).create(
            episode,
            {"storyboard_version": "1", "script": "对白。", "asset_ids": [str(asset.id)]},
            "legacy-dialogue",
        )["shot"]
        with session.begin():
            if dependency == "voice":
                session.add(
                    CharacterVoice(
                        project_id=project,
                        asset_id=asset.id,
                        row_version=1,
                        record_id=1,
                        media_id=1,
                    )
                )
            else:
                session.add(
                    ShotDialogue(
                        shot_id=int(shot["id"]),
                        row_version=1,
                        document={"lines": [{"character_id": str(asset.id), "text": "回来。"}]},
                    )
                )
        with pytest.raises(WorkflowError) as reference:
            assets.patch(asset.id, {"row_version": "1", "kind": "prop"})
        assert reference.value.code == "asset_kind_incompatible"
        with session.begin():
            assert session.get(Asset, asset.id).kind == "character"
            model = CharacterVoice if dependency == "voice" else ShotDialogue
            assert session.scalar(select(model)) is not None
