import asyncio

import httpx
from fastapi import FastAPI

from short_drama.api.v1.project_storyboard import get_storyboard_service, router


def test_static_order_route_and_closed_storyboard_inputs():
    calls = []

    class Service:
        def reorder(self, project_id, payload):
            calls.append(payload)
            return {"storyboard_version": "2", "ordered_ids": ["7"]}

    async def run():
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_storyboard_service] = Service
        url = "/api/v1/projects/2/shots/order"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.put(url, json={"storyboard_version": "1", "shot_ids": ["7"]})
            assert response.status_code == 200
            assert response.json() == {"storyboard_version": "2", "ordered_ids": ["7"]}
            assert (
                await client.put(
                    url,
                    json={"storyboard_version": "1", "shot_ids": ["7", "7"]},
                )
            ).status_code == 422
            assert (
                await client.put(
                    url,
                    json={"storyboard_version": "1", "shot_ids": ["7"], "position": 1},
                )
            ).status_code == 422
        assert len(calls) == 1

    asyncio.run(run())


def test_create_rejects_partial_image_settings_and_oversize_utf8_script():
    class Service:
        def create(self, *_args):
            raise AssertionError("invalid request must not reach service")

    async def run():
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_storyboard_service] = Service
        url = "/api/v1/projects/2/shots"
        headers = {"Idempotency-Key": "closed-input"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            partial = await client.post(
                url,
                headers=headers,
                json={"storyboard_version": "1", "image_settings": {"resolution": "2K"}},
            )
            assert partial.status_code == 422
            oversized = await client.post(
                url,
                headers=headers,
                json={"storyboard_version": "1", "script": "汉" * 10923},
            )
            assert oversized.status_code == 422
            source_excerpt = await client.post(
                url,
                headers=headers,
                json={"storyboard_version": "1", "source_excerpt": "client-owned"},
            )
            assert source_excerpt.status_code == 422
            invalid_duration = await client.post(
                url,
                headers=headers,
                json={"storyboard_version": "1", "duration_ms": 999},
            )
            assert invalid_duration.status_code == 422

    asyncio.run(run())


def test_delete_requires_both_versions_and_returns_decimal_identifiers():
    calls = []

    class Service:
        def delete(self, project_id, shot_id, payload):
            calls.append((project_id, shot_id, payload.model_dump()))
            return {"deleted_id": shot_id, "storyboard_version": 2**60 + 1}

    async def run():
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_storyboard_service] = Service
        url = f"/api/v1/projects/2/shots/{2**60}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            for payload in (
                {},
                {"row_version": "1"},
                {"row_version": True, "storyboard_version": "2"},
            ):
                response = await client.request("DELETE", url, json=payload)
                assert response.status_code == 422
            response = await client.request(
                "DELETE", url, json={"row_version": "1", "storyboard_version": "2"}
            )
            assert response.status_code == 200
            assert response.json() == {
                "deleted_id": str(2**60),
                "storyboard_version": str(2**60 + 1),
            }
        assert calls == [(2, 2**60, {"row_version": 1, "storyboard_version": 2})]

    asyncio.run(run())
