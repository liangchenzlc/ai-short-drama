import asyncio

import httpx
import pytest
from generation_fixtures import generation_session
from sqlalchemy import event

from short_drama.core.exceptions import WorkflowError
from short_drama.service.asset_service import AssetService
from short_drama.service.episode_asset_service import EpisodeAssetService
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService


def setup_storyboard(session):
    project = ProjectService(session).create_project({"name": "storyboard", "aspect": "16:9"})
    episode = EpisodeService(session).create_for_project(
        project.id, {"title": "first", "style": "ink"}
    )
    return project.id, episode.id


def test_storyboard_list_keeps_query_count_bounded_as_shots_grow():
    from short_drama.service.episode_storyboard_service import EpisodeStoryboardService

    with generation_session() as session:
        project_id, episode_id = setup_storyboard(session)
        asset = AssetService(session).create({"kind": "prop", "name": "umbrella"})
        EpisodeAssetService(session).create(
            {"episode_id": episode_id, "asset_id": asset.id, "position": 1}
        )
        service = EpisodeStoryboardService(session)
        version = "1"
        for index in range(3):
            created = service.create(
                project_id,
                episode_id,
                {
                    "storyboard_version": version,
                    "script": f"shot-{index}",
                    "asset_ids": [str(asset.id)] if index == 1 else [],
                    "image_settings": {"resolution": "2K", "aspect": "inherit", "layout": "single"},
                },
                f"shot-{index}",
            )
            version = created["storyboard_version"]
        statements = []

        def count_query(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", count_query)
        try:
            page = service.list(project_id, episode_id)
        finally:
            event.remove(engine, "before_cursor_execute", count_query)

        assert [item["script"] for item in page["items"]] == ["shot-0", "shot-1", "shot-2"]
        assert [item["asset_ids"] for item in page["items"]] == [[], [str(asset.id)], []]
        assert len(statements) <= 6


def test_shot_context_is_canonical_and_ignores_image_settings():
    from short_drama.service.shot_context import (
        compute_shot_context_hash,
        normalize_shot_context,
    )

    assets = [
        {
            "id": 9,
            "kind": "scene",
            "name": "street",
            "label": "outside",
            "description": "rain",
            "prompt": "cold",
            "tags": ["night", "city"],
            "scene_time": "night",
            "state": "confirmed",
            "media_id": 90,
        },
        {"id": 3, "kind": "prop", "name": "umbrella"},
    ]
    context = normalize_shot_context(
        shot_id=7,
        script="  keep whitespace\n",
        duration_ms=5000,
        episode_aspect="16:9",
        episode_style="ink",
        assets=assets,
    )
    assert context == {
        "version": "shot-context-v1",
        "shot_id": "7",
        "shot": {"script": "  keep whitespace\n", "duration_ms": 5000},
        "episode": {"aspect": "16:9", "style": "ink"},
        "assets": [
            {
                "id": "3",
                "kind": "prop",
                "name": "umbrella",
                "label": "",
                "description": "",
                "prompt": "",
                "tags": [],
                "scene_time": "",
                "state": "unconfirmed",
                "media_id": None,
            },
            {
                "id": "9",
                "kind": "scene",
                "name": "street",
                "label": "outside",
                "description": "rain",
                "prompt": "cold",
                "tags": ["city", "night"],
                "scene_time": "night",
                "state": "confirmed",
                "media_id": "90",
            },
        ],
    }
    digest = compute_shot_context_hash(
        shot_id=7,
        script="  keep whitespace\n",
        duration_ms=5000,
        episode_aspect="16:9",
        episode_style="ink",
        assets=reversed(assets),
    )
    assert digest == compute_shot_context_hash(
        shot_id=7,
        script="  keep whitespace\n",
        duration_ms=5000,
        episode_aspect="16:9",
        episode_style="ink",
        assets=assets,
    )
    assert len(digest) == 64 and digest == digest.lower()


def test_create_replay_update_reorder_and_archive_preserve_versions_and_rows():

    from short_drama.domain.shot_script import ShotScript
    from short_drama.service.episode_storyboard_service import EpisodeStoryboardService

    with generation_session() as session:
        project_id, episode_id = setup_storyboard(session)
        service = EpisodeStoryboardService(session)
        payload = {
            "storyboard_version": "1",
            "script": "",
            "asset_ids": [],
            "image_settings": {"resolution": "2K", "aspect": "inherit", "layout": "single"},
        }
        first = service.create(project_id, episode_id, payload, " create-1 ")
        assert first["created"] is True
        assert first["storyboard_version"] == "2"
        assert first["shot"]["row_version"] == "1"
        assert first["shot"]["duration_ms"] == 3000
        assert first["shot"]["source_excerpt"] == ""
        replay = service.create(project_id, episode_id, payload, "create-1")
        assert replay["created"] is False
        assert replay["shot"]["id"] == first["shot"]["id"]
        assert replay["storyboard_version"] == "2"

        with pytest.raises(WorkflowError) as conflict:
            service.create(project_id, episode_id, {**payload, "script": "different"}, "create-1")
        assert conflict.value.code == "idempotency_conflict"
        with pytest.raises(WorkflowError) as duration_conflict:
            service.create(project_id, episode_id, {**payload, "duration_ms": 5000}, "create-1")
        assert duration_conflict.value.code == "idempotency_conflict"

        same = service.update(
            project_id,
            episode_id,
            first["shot"]["id"],
            {"row_version": "1", "script": ""},
        )
        assert same["shot"]["row_version"] == "1"
        assert same["storyboard_version"] == "2"
        changed = service.update(
            project_id,
            episode_id,
            first["shot"]["id"],
            {"row_version": "1", "script": "close-up", "duration_ms": 5000},
        )
        assert changed["shot"]["row_version"] == "2"
        assert changed["shot"]["duration_ms"] == 5000
        assert changed["shot"]["source_excerpt"] == ""
        assert changed["storyboard_version"] == "3"
        with pytest.raises(WorkflowError) as stale:
            service.update(
                project_id,
                episode_id,
                first["shot"]["id"],
                {"row_version": "1", "script": "lost edit"},
            )
        assert stale.value.code == "shot_version_conflict"

        second = service.create(
            project_id,
            episode_id,
            {**payload, "storyboard_version": "3", "script": "wide"},
            "create-2",
        )
        ordered = service.reorder(
            project_id,
            episode_id,
            {
                "storyboard_version": second["storyboard_version"],
                "shot_ids": [second["shot"]["id"], first["shot"]["id"]],
            },
        )
        assert ordered["ordered_ids"] == [second["shot"]["id"], first["shot"]["id"]]
        assert ordered["storyboard_version"] == "5"

        current = service.get(project_id, episode_id, first["shot"]["id"])
        service.archive(
            project_id,
            episode_id,
            first["shot"]["id"],
            current["shot"]["row_version"],
        )
        assert service.list(project_id, episode_id)["total"] == 1
        history = service.list(project_id, episode_id, include_archived=True)
        assert history["total"] == 2
        archived = next(item for item in history["items"] if item["id"] == first["shot"]["id"])
        assert archived["deleted_at"] is not None
        with session.begin():
            assert session.get(ShotScript, int(first["shot"]["id"])) is not None


def test_assets_must_belong_to_episode_and_change_context_hash():
    from short_drama.service.episode_storyboard_service import EpisodeStoryboardService

    with generation_session() as session:
        project_id, episode_id = setup_storyboard(session)
        asset = AssetService(session).create({"kind": "prop", "name": "umbrella"})
        EpisodeAssetService(session).create(
            {"episode_id": episode_id, "asset_id": asset.id, "position": 1}
        )
        foreign = AssetService(session).create({"kind": "prop", "name": "lamp"})
        service = EpisodeStoryboardService(session)
        created = service.create(
            project_id,
            episode_id,
            {
                "storyboard_version": "1",
                "script": "rain",
                "asset_ids": [str(asset.id)],
                "image_settings": {"resolution": "2K", "aspect": "inherit", "layout": "single"},
            },
            "asset-shot",
        )
        original_hash = created["shot"]["context_hash"]
        assert (
            original_hash
            == service.get_shot_context(project_id, episode_id, created["shot"]["id"])[
                "context_hash"
            ]
        )
        with pytest.raises(WorkflowError) as invalid:
            service.update(
                project_id,
                episode_id,
                created["shot"]["id"],
                {"row_version": "1", "asset_ids": [str(foreign.id)]},
            )
        assert invalid.value.code == "not_found"
        changed = service.update(
            project_id,
            episode_id,
            created["shot"]["id"],
            {
                "row_version": "1",
                "image_settings": {"resolution": "4K", "aspect": "1:1", "layout": "nine"},
            },
        )
        assert changed["shot"]["context_hash"] == original_hash


def test_http_contract_for_storyboard_routes():
    from fastapi import FastAPI

    from short_drama.api.v1.episode_storyboard import get_storyboard_service, router

    calls = []
    shot = {
        "id": "7",
        "position": 1,
        "script": "",
        "duration_ms": 3000,
        "source_excerpt": "",
        "row_version": "1",
        "asset_ids": [],
        "image_settings": {"resolution": "2K", "aspect": "inherit", "layout": "single"},
        "context_hash": "a" * 64,
        "image": None,
        "deleted_at": None,
    }

    class Service:
        def list(self, project_id, episode_id, **kwargs):
            return {
                "episode_id": str(episode_id),
                "storyboard_version": "1",
                "items": [],
                "total": 0,
                "offset": kwargs["offset"],
                "limit": kwargs["limit"],
            }

        def get(self, project_id, episode_id, shot_id):
            return {"shot": shot, "storyboard_version": "1"}

        def create(self, project_id, episode_id, payload, key):
            calls.append(key)
            return {"shot": shot, "storyboard_version": "2", "created": key == "new"}

        def update(self, project_id, episode_id, shot_id, payload):
            return {"shot": shot, "storyboard_version": "2"}

        def reorder(self, project_id, episode_id, payload):
            return {"storyboard_version": "2", "ordered_ids": ["7"]}

        def archive(self, project_id, episode_id, shot_id, version):
            calls.append(version)

    async def run():
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_storyboard_service] = Service
        root = "/api/v1/projects/1/episodes/2/shots"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get(root)).status_code == 200
            assert (await client.get(root + "/7")).status_code == 200
            assert (await client.post(root, json={"storyboard_version": "1"})).status_code == 422
            assert (
                await client.post(
                    root,
                    headers={"Idempotency-Key": "new"},
                    json={"storyboard_version": "1"},
                )
            ).status_code == 201
            assert (
                await client.post(
                    root,
                    headers={"Idempotency-Key": "replay"},
                    json={"storyboard_version": "1"},
                )
            ).status_code == 200
            assert (
                await client.patch(
                    root + "/7", json={"row_version": "1", "script": "x", "duration_ms": 5000}
                )
            ).status_code == 200
            assert (
                await client.patch(
                    root + "/7", json={"row_version": "1", "source_excerpt": "forged"}
                )
            ).status_code == 422
            assert (
                await client.put(
                    root + "/order",
                    json={"storyboard_version": "1", "shot_ids": ["7"]},
                )
            ).status_code == 200
            assert (
                await client.delete(root + "/7", headers={"If-Match": '"1"'})
            ).status_code == 204
            assert (await client.delete(root + "/7")).status_code == 422
        assert calls == ["new", "replay", 1]

    asyncio.run(run())


def test_legacy_shot_services_advance_versions_and_archive_instead_of_deleting():
    from short_drama.service.shot_script_service import ShotScriptService

    with generation_session() as session:
        project_id, episode_id = setup_storyboard(session)
        shots = ShotScriptService(session)
        created = shots.create({"episode_id": episode_id, "position": 1, "script": "one"})
        episode = EpisodeService(session).get(episode_id)
        assert episode.storyboard_version == 2
        assert created.row_version == 1
        changed = shots.update(created.id, {"script": "two"})
        assert changed.row_version == 2
        assert EpisodeService(session).get(episode_id).storyboard_version == 3
        same = shots.update(created.id, {"script": "two"})
        assert same.row_version == 2
        shots.delete(created.id)
        assert shots.list().total == 0
        assert shots.get(created.id).deleted_at is not None
        assert EpisodeService(session).get(episode_id).storyboard_version == 4


def test_internal_asset_and_media_writes_advance_shot_and_storyboard_once():
    from short_drama.service.media_file_service import MediaFileService
    from short_drama.service.shot_asset_service import ShotAssetService
    from short_drama.service.shot_image_service import ShotImageService
    from short_drama.service.shot_script_service import ShotScriptService

    with generation_session() as session:
        project_id, episode_id = setup_storyboard(session)
        shot = ShotScriptService(session).create({"episode_id": episode_id, "position": 1})
        asset = AssetService(session).create({"kind": "prop", "name": "umbrella"})
        EpisodeAssetService(session).create(
            {"episode_id": episode_id, "asset_id": asset.id, "position": 1}
        )
        link = ShotAssetService(session).create(
            {"episode_id": episode_id, "shot_id": shot.id, "asset_id": asset.id}
        )
        assert ShotScriptService(session).get(shot.id).row_version == 2
        assert EpisodeService(session).get(episode_id).storyboard_version == 3

        media = MediaFileService(session).create(
            {"format_code": "image/png", "storage_locator": "test/storyboard.png"}
        )
        image = ShotImageService(session).create(
            {
                "episode_id": episode_id,
                "shot_id": shot.id,
                "media_id": media.id,
                "aspect": "16:9",
                "context_hash": "a" * 64,
            }
        )
        assert ShotScriptService(session).get(shot.id).row_version == 3
        assert EpisodeService(session).get(episode_id).storyboard_version == 4
        ShotImageService(session).update(image.id, {"prompt": ""})
        assert ShotScriptService(session).get(shot.id).row_version == 3
        ShotAssetService(session).delete(link.id)
        assert ShotScriptService(session).get(shot.id).row_version == 4
        assert EpisodeService(session).get(episode_id).storyboard_version == 5


def test_legacy_generated_shot_batch_advances_collection_once():
    from short_drama.service.episode_script_service import EpisodeScriptService
    from short_drama.service.generation_service import GenerationService

    with generation_session() as session:
        project_id, episode_id = setup_storyboard(session)
        script = EpisodeScriptService(session).create(
            {"episode_id": episode_id, "position": 1, "content": "source"}
        )
        result = GenerationService(session).generate_shots(script.id, ["one", "two"], batch_id=123)
        assert len(result.outputs) == 2
        assert EpisodeService(session).get(episode_id).storyboard_version == 2
