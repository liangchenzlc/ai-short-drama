"""HTTP + disposable MySQL + real worker logic; no paid model calls."""

import asyncio
import base64
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from time import monotonic

import httpx
import pytest
from PIL import Image
from sqlalchemy import inspect
from test_asset_image_generation import drain
from test_asset_image_generation import flow as flow

from short_drama.ai import GenerationResult
from short_drama.api.dependencies import get_session
from short_drama.domain import MediaFile
from short_drama.main import create_app
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.service.shot_image_service import ShotImageService

pytestmark = pytest.mark.integration


class VideoProvider:
    def __init__(self):
        self.calls = []

    def validate(self, *_):
        return {}

    def submit(self, snapshot, request, *_args, reference_loader=None, **_kwargs):
        self.calls.append(deepcopy(request))
        assert reference_loader is not None
        frame = reference_loader(0, 10 * 1024**2, monotonic() + 10)
        with Image.open(BytesIO(frame)) as image:
            assert image.size == (16, 9)
        # Container bytes test archival only; browser playback uses a real fixture.
        data = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 24
        return GenerationResult(
            status="succeeded",
            adapter=_kwargs["adapter"],
            outputs=[{"base64": base64.b64encode(data).decode()}],
        )


@pytest.mark.parametrize(
    "layout",
    ["four", "five"],
)
def test_video_http_generation_history_adoption_and_staleness(flow, layout):
    model = AIModelConfigService(flow.session).create(
        {
            "service_type": "video",
            "name": "controlled video",
            "provider": "modelhub",
            "model_key": "seedance-2.0-mini",
            "base_url": "https://api.modelhub.cc",
        }
    )
    service = EpisodeStoryboardService(flow.session)
    shot = service.create(
        flow.project.id,
        flow.episode.id,
        {
            "storyboard_version": "1",
            "script": "人物右手拿起桌上的伞，机位保持固定。",
            "duration_ms": 5000,
        },
        "video-fixture",
    )["shot"]
    stream = BytesIO()
    Image.new("RGB", (16, 9), "blue").save(stream, "PNG")
    frame = stream.getvalue()
    bucket = flow.settings.minio_image_bucket
    flow.storage.put(bucket, "frame.png", BytesIO(frame), len(frame), "image/png")
    flow.session.add(
        MediaFile(
            id=101,
            format_code="image/png",
            original_name="",
            storage_locator=f"minio://{bucket}/frame.png",
            width=16,
            height=9,
        )
    )
    flow.session.commit()
    ShotImageService(flow.session).create(
        {
            "episode_id": flow.episode.id,
            "shot_id": shot["id"],
            "media_id": "101",
            "layout": layout,
            "resolution": "2K",
            "aspect": "16:9",
            "context_hash": shot["context_hash"],
        }
    )
    flow.provider = VideoProvider()
    flow.executor = GenerationExecutionService(
        flow.factory, flow.settings, flow.provider, flow.storage
    )

    async def run():
        app = create_app(flow.settings)
        app.state.settings, app.state.storage = flow.settings, flow.storage

        def session():
            with flow.factory() as database:
                yield database

        app.dependency_overrides[get_session] = session
        root = f"/api/v1/projects/{flow.project.id}/episodes/{flow.episode.id}/shots/{shot['id']}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            latest = (await client.get(root)).json()["shot"]
            body = {
                "config_id": str(model.id),
                "source": {
                    "scene": "shot_video",
                    "shot_id": shot["id"],
                    "row_version": latest["row_version"],
                    "context_hash": latest["video_context_hash"],
                    "reference_media_id": "101",
                },
            }
            headers = {"Idempotency-Key": "video-http"}
            created = await client.post("/api/v1/ai/generations/video", json=body, headers=headers)
            assert created.status_code == 202, created.text
            task_id = created.json()["generation_id"]
            assert (
                await client.post("/api/v1/ai/generations/video", json=body, headers=headers)
            ).status_code == 200
            assert drain(flow, task_id) == "succeeded"
            assert len(flow.provider.calls) == 1
            filters = {"source_scene": "shot_video", "source_id": shot["id"]}
            history = await client.get(
                "/api/v1/ai/generations", params={**filters, "service_type": "video"}
            )
            assert history.status_code == 200 and history.json()["total"] == 1
            candidates = await client.get(
                "/api/v1/media-library/items", params={**filters, "media_type": "video"}
            )
            assert candidates.status_code == 200, candidates.text
            candidate = candidates.json()["items"][0]
            before = (await client.get(root)).json()["shot"]
            assert before["video"] is None
            adopted = await client.post(
                f"/api/v1/media-library/items/{candidate['asset_id']}/apply",
                json={
                    "target": {"type": "shot_video", "id": shot["id"]},
                    "expected_media_id": None,
                    "expected_row_version": before["row_version"],
                    "expected_context_hash": before["video_context_hash"],
                },
            )
            assert adopted.status_code == 200, adopted.text
            before = (await client.get(root)).json()["shot"]
            assert not before["video"]["is_stale"] and not before["image"]["is_stale"]
            changed = await client.patch(
                root,
                json={"row_version": before["row_version"], "video_prompt": "拿伞时镜头缓慢推近。"},
            )
            assert changed.status_code == 200, changed.text
            after = changed.json()["shot"]
            assert after["video"]["is_stale"] and not after["image"]["is_stale"]
            assert after["video_prompt"] == "拿伞时镜头缓慢推近。"

    asyncio.run(run())


def test_video_migration_upgrades_previous_schema(migration_mysql_engine):
    engine = migration_mysql_engine
    # Recreate the pre-video schema on a disposable database, then apply migration.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE shot_videos DROP FOREIGN KEY fk_shot_videos_first_frame, "
            "DROP KEY idx_shot_videos_first_frame, DROP COLUMN first_frame_media_id, "
            "DROP COLUMN context_hash"
        )
        connection.exec_driver_sql(
            "ALTER TABLE shot_scripts DROP CHECK ck_shot_scripts_video_settings, "
            "DROP COLUMN video_prompt, DROP COLUMN video_settings"
        )
        migration = (
            Path(__file__).resolve().parents[3]
            / "docs/数据库模型/migrations/2026-09-28-shot-video/001_shot_video.sql"
        )
        source = "\n".join(
            line
            for line in migration.read_text(encoding="utf-8").splitlines()
            if not line.startswith("--")
        )
        for statement in source.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        assert {"video_prompt", "video_settings"} <= {
            item["name"] for item in inspect(connection).get_columns("shot_scripts")
        }
        assert {"context_hash", "first_frame_media_id"} <= {
            item["name"] for item in inspect(connection).get_columns("shot_videos")
        }
