import asyncio

import httpx
from fastapi import FastAPI

from short_drama.api.dependencies import get_session
from short_drama.api.v1 import episode_generation


def test_extraction_routes_require_explicit_adoption_and_closed_inputs(monkeypatch):
    calls = []

    class Service:
        def __init__(self, _session):
            pass

        def get(self, project_id, episode_id, generation_id):
            return {"generation_id": str(generation_id), "items": [], "stale": False}

        def patch(self, project_id, episode_id, generation_id, payload):
            calls.append(("patch", payload.items[0].draft.prompt))
            return {"result_version": "2"}

        def apply(self, project_id, episode_id, generation_id, payload, key):
            calls.append(("apply", key))
            assert payload.content_version == 3
            return {"created": 1, "reused": 0}

    monkeypatch.setattr(episode_generation, "AssetExtractionService", Service)

    async def run():
        app = FastAPI()
        app.include_router(episode_generation.router, prefix="/api/v1")
        app.dependency_overrides[get_session] = lambda: object()
        root = "/api/v1/projects/1/episodes/2/asset-extraction-results/99999999999999999"
        cid = "a" * 32
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(root)
            assert response.json()["generation_id"] == "99999999999999999"
            patch = {
                "result_version": "1",
                "items": [
                    {
                        "candidate_id": cid,
                        "draft": {
                            "kind": "prop",
                            "name": "伞",
                            "description": "红色雨伞",
                            "prompt": "红伞",
                        },
                    }
                ],
            }
            assert (await client.patch(root, json=patch)).status_code == 200
            patch["items"][0]["draft"]["media_id"] = "8"
            assert (await client.patch(root, json=patch)).status_code == 422
            adoption = {
                "result_version": "2",
                "content_version": "3",
                "items": [
                    {
                        "candidate_id": cid,
                        "action": "create",
                    }
                ],
            }
            assert (await client.post(root + "/apply", json=adoption)).status_code == 422
            response = await client.post(
                root + "/apply", json=adoption, headers={"Idempotency-Key": "apply-key"}
            )
            assert response.status_code == 200 and response.json()["created"] == 1
            adoption["items"][0]["action"] = "reuse"
            assert (
                await client.post(
                    root + "/apply", json=adoption, headers={"Idempotency-Key": "reuse-key"}
                )
            ).status_code == 422
        assert calls == [("patch", "红伞"), ("apply", "apply-key")]

    asyncio.run(run())
