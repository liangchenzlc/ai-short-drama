"""本地 ASGI 图片解析合同；不启动原 API 或存储服务。"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from short_drama.api.dependencies import get_session
from short_drama.api.v1.canvas_character_images import router
from short_drama.core.exceptions import BusinessError
from short_drama.service.canvas_character_catalog_service import (
    CanvasCharacterCatalogService,
)
from tests.catalog_fixtures import ASSET, KEY, SOURCE

pytest_plugins = ("tests.catalog_fixtures",)
PATH = f"/api/v1/canvas-runtime/canvas-projects/{KEY}/character-catalog/{ASSET}/image-resources"


@pytest.fixture
def runtime(catalog_session):
    session, _ = catalog_session
    expected = CanvasCharacterCatalogService(session).get(KEY, ASSET).item.current_version_id
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        minio_image_bucket="fixture-only",
        minio_video_bucket="video",
        minio_audio_bucket="audio",
    )
    app.include_router(router, prefix="/api/v1")

    def provided_session():
        yield session
        assert not session.in_transaction() and not session.dirty and not session.new

    app.dependency_overrides[get_session] = provided_session

    @app.exception_handler(BusinessError)
    async def business_error(_request: Request, exc: BusinessError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    def request(params=None, **kwargs):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://asgi.fixture"
            ) as client:
                return await client.get(PATH, params=params, **kwargs)

        return asyncio.run(run())

    return request, session, expected


def test_minimal_resource_http_contract(runtime):
    request, _, expected = runtime
    response = request(
        {"expected_version_id": str(expected), "source_project_id": "999"},
        headers={"X-Canvas-Actor": "1"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_project_id"] == str(SOURCE) and data["version_id"] == str(expected)
    assert len(data["images"]) == 2 and data["images"][0]["resource"]["mimeType"] == "image/png"
    assert "minio://" not in response.text


@pytest.mark.parametrize("value", [None, "0", "-1", "1.2", "18446744073709551616"])
def test_expected_version_is_required_positive_uint64(runtime, value):
    request, _, _ = runtime
    response = request({} if value is None else {"expected_version_id": value})
    assert response.status_code == 422


def test_stale_version_actor_switch_and_anonymous_are_explicit(runtime):
    request, session, expected = runtime
    assert request({"expected_version_id": str(expected + 1)}).status_code == 409
    assert (
        request({"expected_version_id": str(expected)}, headers={"X-Canvas-Actor": "2"}).json()[
            "error"
        ]["code"]
        == "canvas_actor_changed"
    )
    session.info.pop("actor")
    assert request({"expected_version_id": str(expected)}).status_code == 401


@pytest.mark.parametrize("policy,status", [("pinned", 200), ("current", 200), ("invalid", 422)])
def test_image_version_policy_contract(runtime, policy, status):
    request, _, expected = runtime
    assert (
        request({"expected_version_id": str(expected), "version_policy": policy}).status_code
        == status
    )
