import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from short_drama.api.dependencies import get_session
from short_drama.api.v1.episode_generation import router
from short_drama.schemas.episode_storyboard import StoryboardResultPage


def preview_payload():
    return {
        "generation_id": "9007199254740993",
        "items": [
            {
                "position": 21,
                "title": "next shot",
                "script": "opening",
                "duration_ms": 4500,
                "asset_ids": ["9007199254740995"],
                "source_excerpt": "source",
                "story_beat": "turn",
                "assets": [
                    {
                        "id": "9007199254740995",
                        "kind": "scene",
                        "name": "frozen name",
                        "available": False,
                        "snapshot_missing": False,
                    }
                ],
            }
        ],
        "total": 25,
        "total_duration_ms": 100000,
        "offset": 20,
        "limit": 20,
        "applied": None,
    }


def test_storyboard_preview_http_contract_serializes_large_identifiers_and_complete_fields(
    monkeypatch,
):
    calls = []

    class Service:
        def __init__(self, _session):
            pass

        def storyboard_result_page(self, *args):
            calls.append(args)
            return preview_payload()

    monkeypatch.setattr("short_drama.api.v1.episode_generation.GenerationBusinessService", Service)

    async def run():
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_session] = lambda: None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            path = "/api/v1/projects/1/episodes/2/storyboard-results/9007199254740993/shots"
            response = await client.get(path, params={"offset": 20, "limit": 20})
            assert response.status_code == 200
            assert response.json() == preview_payload()
            assert (await client.get(path, params={"offset": -1})).status_code == 422
            assert (await client.get(path, params={"limit": 101})).status_code == 422
        assert calls == [(1, 2, 9007199254740993, 20, 20)]

    asyncio.run(run())


@pytest.mark.parametrize("duration", [999, 10001])
def test_storyboard_preview_rejects_out_of_range_candidate_durations(duration):
    payload = preview_payload()
    payload["items"][0]["duration_ms"] = duration
    with pytest.raises(ValidationError):
        StoryboardResultPage.model_validate(payload)
