from io import BytesIO
from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session, settings
from PIL import Image
from sqlalchemy import func, select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIGenerationRecord, AssetImageCandidate, AsyncTask, MediaFile
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.generation_business_service import GenerationBusinessService
from short_drama.service.generation_reference_service import GenerationReferenceService
from short_drama.service.project_service import ProjectService


class MemoryStorage:
    def __init__(self):
        self.count = 0
        self.deleted = []

    def upload(self, _stream, **_kwargs):
        self.count += 1
        return SimpleNamespace(
            storage_locator=f"minio://image/ref-{self.count}.png", version_id="1"
        )

    def download_url(self, locator):
        return f"https://fixture.test/{locator.rsplit('/', 1)[-1]}"

    def delete(self, locator, **_kwargs):
        self.deleted.append(locator)


def png():
    stream = BytesIO()
    Image.new("RGB", (4, 4), "green").save(stream, format="PNG")
    return stream.getvalue()


def workspace(session):
    project = ProjectService(session).create_project({"name": "雨夜", "aspect": "16:9"})
    episode = EpisodeService(session).create_for_project(project.id, {"title": "归来"})
    return project.id, episode.id


def test_asset_references_persist_deduplicate_freeze_into_requests_and_never_adopt():
    with generation_session() as session:
        config(session)
        project_id, episode_id = workspace(session)
        asset, _ = AssetLibraryService(session).create(
            "episode",
            episode_id,
            project_id,
            {"kind": "character", "name": "林晚", "description": "黑发"},
            "new-asset",
        )
        storage = MemoryStorage()
        data = png()
        service = GenerationReferenceService(session, settings, storage, "asset", asset.row_version)
        result, created = service.upload(asset.id, BytesIO(data), len(data), "人物.png")
        assert created and result["row_version"] == "2"
        media_id = result["items"][0]["media_id"]
        reopened = GenerationReferenceService(session, settings, storage, "asset", "2")
        assert reopened.list_references(asset.id)["items"][0]["media_id"] == media_id
        same, created = reopened.upload(asset.id, BytesIO(data), len(data), "人物.png")
        assert not created and same["row_version"] == "2"
        assert storage.deleted == ["minio://image/ref-2.png"]
        with pytest.raises(WorkflowError, match="内容已修改"):
            service.upload(asset.id, BytesIO(data), len(data), "人物.png")
        assert storage.count == 2  # stale version rejected before upload
        task, _ = AIGenerationService(session, settings).create(
            "image",
            {
                "input": {"prompt": ""},
                "source": {
                    "scene": "asset_image",
                    "asset_id": str(asset.id),
                    "row_version": "2",
                    "project_id": str(project_id),
                    "episode_id": str(episode_id),
                },
            },
            "reference-generation",
        )
        record = session.scalar(select(AIGenerationRecord))
        assert record.request_data["input"]["reference_media_ids"] == [media_id]
        assert task["display_context"]["subject"] == "角色：林晚"
        assert task["display_context"]["project"] == "雨夜"
        assert "第 1 集" in task["display_context"]["episode"]
        assert session.scalar(select(func.count()).select_from(AssetImageCandidate)) == 0
        session.commit()
        removed = reopened.remove_reference(asset.id, media_id)
        assert removed == {"row_version": "3", "items": []}
        assert session.get(MediaFile, int(media_id)) is not None


def test_shot_reference_changes_context_and_saved_generation_includes_it():
    with generation_session() as session:
        config(session)
        project_id, episode_id = workspace(session)
        storyboard = EpisodeStoryboardService(session)
        original = storyboard.create(
            project_id,
            episode_id,
            {"storyboard_version": "1", "script": "林晚走入雨夜"},
            "new-shot",
        )
        shot = original["shot"]
        data = png()
        result, _ = GenerationReferenceService(
            session, settings, MemoryStorage(), "shot", "1"
        ).upload(shot["id"], BytesIO(data), len(data), "雨夜.png")
        current = storyboard.get(project_id, episode_id, shot["id"])["shot"]
        assert current["context_hash"] != shot["context_hash"]
        assert current["row_version"] == "2"
        task, _ = AIGenerationService(session, settings).create(
            "image",
            {
                "input": {"prompt": ""},
                "source": {
                    "scene": "shot_image",
                    "shot_id": shot["id"],
                    "context_mode": "saved",
                    "context_hash": current["context_hash"],
                    "row_version": "2",
                    "layout": "single",
                },
                "parameters": {"aspect": "16:9", "resolution": "2K", "count": 1},
            },
            "shot-reference-generation",
        )
        record = session.scalar(select(AIGenerationRecord))
        assert record.request_data["input"]["reference_media_ids"] == [
            result["items"][0]["media_id"]
        ]
        assert task["display_context"]["subject"] == "分镜 01 生图"


def test_storyboard_history_pages_only_requested_shots_and_checks_episode_scope():
    with generation_session() as session:
        config(session, "text")
        project_id, episode_id = workspace(session)
        task, _ = AIGenerationService(session, settings).create(
            "text",
            {
                "input": {"messages": [{"role": "user", "content": "fixture"}]},
            },
            "history-fixture",
        )
        record = session.scalar(select(AIGenerationRecord))
        record.request_data = {
            "source": {
                "scene": "script_shots",
                "project_id": str(project_id),
                "episode_id": str(episode_id),
            }
        }
        record.response_data = {
            "business_result": {
                "kind": "script_shots",
                "shots": [
                    {"script": f"shot {index}", "source_excerpt": "hidden", "asset_ids": []}
                    for index in range(55)
                ],
            }
        }
        from short_drama.dao.task_runtime_dao import finish

        finish(session.get(AsyncTask, int(task["generation_id"])), "succeeded")
        session.commit()
        service = GenerationBusinessService(session)
        page = service.storyboard_result_page(project_id, episode_id, task["generation_id"], 20, 20)
        assert page["total"] == 55 and len(page["items"]) == 20
        assert page["items"][0] == {"position": 21, "script": "shot 20"}
        assert (
            len(
                service.storyboard_result_page(
                    project_id, episode_id, task["generation_id"], 40, 20
                )["items"]
            )
            == 15
        )
        with pytest.raises(WorkflowError, match="不属于"):
            service.storyboard_result_page(project_id, episode_id + 1, task["generation_id"])


def test_move_swaps_neighbor_without_client_loading_the_entire_board():
    with generation_session() as session:
        project_id, episode_id = workspace(session)
        service = EpisodeStoryboardService(session)
        version = "1"
        ids = []
        for index in range(3):
            result = service.create(
                project_id,
                episode_id,
                {"storyboard_version": version, "script": str(index)},
                str(index),
            )
            version = result["storyboard_version"]
            ids.append(result["shot"]["id"])
        moved = service.move(
            project_id, episode_id, ids[0], {"storyboard_version": version, "direction": 1}
        )
        assert [item["id"] for item in service.list(project_id, episode_id)["items"]] == [
            ids[1],
            ids[0],
            ids[2],
        ]
        assert moved["storyboard_version"] != version
        with pytest.raises(WorkflowError):
            service.move(
                project_id, episode_id, ids[0], {"storyboard_version": version, "direction": -1}
            )
