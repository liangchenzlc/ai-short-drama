import asyncio

import httpx
from fastapi import FastAPI

from short_drama.api.dependencies import (
    get_ai_generation_service,
    get_user_creation_draft_service,
)
from short_drama.api.v1.ai_generations import router as generations_router
from short_drama.core.config import Settings
from short_drama.main import create_app
from short_drama.service.user_creation_draft_service import UserCreationDraftService


def test_idempotency_lookup_is_read_only_and_validates_query_before_service():
    calls = []

    class Generations:
        def lookup_by_key(self, kind, key):
            calls.append((kind, key))
            return {"generation_id": "9007199254740993"} if key == "accepted" else None

    async def run():
        app = FastAPI()
        app.include_router(generations_router, prefix="/api/v1")
        app.dependency_overrides[get_ai_generation_service] = Generations
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            path = "/api/v1/ai/generations/by-idempotency-key"
            found = await client.get(
                path, params={"service_type": "image", "idempotency_key": "accepted"}
            )
            assert found.status_code == 200
            assert found.json()["generation_id"] == "9007199254740993"
            missing = await client.get(
                path, params={"service_type": "image", "idempotency_key": "unknown"}
            )
            assert missing.status_code == 200 and missing.json() is None
            for params in (
                {"service_type": "invalid", "idempotency_key": "accepted"},
                {"service_type": "image", "idempotency_key": ""},
                {"service_type": "image", "idempotency_key": "x" * 129},
            ):
                assert (await client.get(path, params=params)).status_code == 422
            assert (await client.post(path)).status_code == 405
        assert calls == [("image", "accepted"), ("image", "unknown")]

    asyncio.run(run())


def test_private_draft_http_contract_keeps_scope_on_route_and_preserves_large_ids():
    calls = []
    row = {
        "id": "9007199254740993",
        "user_id": "4",
        "project_id": "1",
        "object_id": "9007199254740995",
        "purpose": "storyboard_image",
        "model_config_id": None,
        "input_text": "侧光",
        "image_settings": {"count": 2},
        "video_settings": None,
        "reference_media_ids": [],
        "reference_images": [],
        "draft_version": "9007199254740997",
        "source_version": "3",
        "row_version": "9007199254740997",
        "created_at": "2026-10-13T01:02:03Z",
        "updated_at": "2026-10-13T01:02:03Z",
    }

    class Drafts:
        def get_for_scope(self, *args):
            calls.append(args)
            return None

        def create_for_scope(self, project, body):
            calls.append((project, body.model_dump()))
            return {**row, "purpose": body.purpose, "input_text": body.input_text}

        def update_for_scope(self, project, obj, purpose, body):
            # 只替换存储层，实际执行分用途校验和应用的 HTTP 异常处理。
            UserCreationDraftService(None)._validate_content(
                purpose, body.model_dump(exclude_unset=True), project, 4
            )
            calls.append((project, obj, purpose, body.row_version))
            return {**row, "purpose": purpose, "input_text": body.input_text or row["input_text"]}

    async def run():
        app = create_app(Settings(_env_file=None))
        app.dependency_overrides[get_user_creation_draft_service] = Drafts
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            path = "/api/v1/projects/1/drafts"
            target = path + "/9007199254740995/storyboard_image"
            assert (await client.get(target)).json() is None
            body = {"object_id": row["object_id"], "purpose": "storyboard_image"}
            created = await client.post(path, json=body)
            assert created.status_code == 201
            assert created.json()["id"] == row["id"]
            assert created.json()["draft_version"] == row["draft_version"]
            assert created.json()["created_at"] == row["created_at"]
            updated = await client.patch(target, json={"row_version": row["row_version"]})
            assert updated.status_code == 200
            assert (await client.patch(target, json={"input_text": "new"})).status_code == 422
            assert (await client.post(path, json={**body, "user_id": "9"})).status_code == 422
            assert (await client.get(path + "/2/not_a_purpose")).status_code == 422
            for purpose in ("novel_adaptation", "asset_image", "storyboard_image"):
                rejected = await client.post(
                    path, json={**body, "purpose": purpose, "input_text": "x" * 4001}
                )
                assert rejected.status_code == 422
                assert "x" * 4001 not in rejected.text
            video_body = {**body, "purpose": "storyboard_video", "input_text": "x" * 16000}
            video = await client.post(path, json=video_body)
            assert video.status_code == 201
            assert len(video.json()["input_text"]) == 16000
            assert (
                await client.patch(
                    target, json={"row_version": row["row_version"], "input_text": "x" * 4001}
                )
            ).status_code == 422
            video_target = path + "/9007199254740995/storyboard_video"
            saved_video = await client.patch(
                video_target,
                json={"row_version": row["row_version"], "input_text": "x" * 16000},
            )
            assert saved_video.status_code == 200
            assert len(saved_video.json()["input_text"]) == 16000
            assert (
                await client.patch(
                    video_target,
                    json={"row_version": row["row_version"], "input_text": "x" * 16001},
                )
            ).status_code == 422
        assert calls[-1] == (1, 9007199254740995, "storyboard_video", 9007199254740997)

    asyncio.run(run())
