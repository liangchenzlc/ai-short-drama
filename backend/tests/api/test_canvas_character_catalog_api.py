"""HTTP 合同使用本地 ASGI 与 SQLite，不启动原 API 或外部服务。"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from short_drama.api.dependencies import get_session
from short_drama.api.v1.canvas_character_catalog import router
from short_drama.core.exceptions import BusinessError
from short_drama.domain import ProjectCanvas
from tests.catalog_fixtures import (
    ASSET,
    CANVAS,
    KEY,
    SOURCE,
)

pytest_plugins = ("tests.catalog_fixtures",)

PATH = f"/api/v1/canvas-runtime/canvas-projects/{KEY}/character-catalog"


@pytest.fixture
def runtime(catalog_session):
    session, _ = catalog_session
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def provided_session():
        yield session
        assert not session.in_transaction()
        assert not session.new and not session.dirty and not session.deleted

    app.dependency_overrides[get_session] = provided_session

    @app.exception_handler(BusinessError)
    async def business_error(_request: Request, exc: BusinessError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    def request(path=PATH, **kwargs):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://asgi.fixture"
            ) as client:
                return await client.get(path, **kwargs)

        return asyncio.run(run())

    return request, session


def test_list_and_detail_http_use_real_string_ids_and_minimal_dto(runtime):
    request, _ = runtime
    listing = request(headers={"X-Canvas-Actor": "1"})
    assert listing.status_code == 200, listing.text
    result = listing.json()
    assert result["source_project_id"] == str(SOURCE)
    assert result["items"][0]["asset_id"] == str(ASSET)
    detail = request(PATH + "/" + str(ASSET))
    assert detail.status_code == 200, detail.text
    assert detail.json()["item"] == result["items"][0]
    assert set(detail.json()) == {"canvas_key", "canvas_row_version", "source_project_id", "item"}


def test_anonymous_and_actor_switch_are_rejected(runtime):
    request, session = runtime
    switched = request(headers={"X-Canvas-Actor": "2"})
    assert switched.status_code == 409
    assert switched.json()["error"]["code"] == "canvas_actor_changed"
    session.info.pop("actor")
    anonymous = request()
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize("identifier", ["0", "-1", "1.2", "18446744073709551616"])
def test_detail_id_validation_precedes_read(runtime, identifier):
    request, _ = runtime
    response = request(PATH + "/" + identifier)
    assert response.status_code == 422


def test_client_source_query_cannot_replace_trusted_source(runtime):
    request, _ = runtime
    response = request(params={"source_project_id": "999"})
    assert response.status_code == 200
    assert response.json()["source_project_id"] == str(SOURCE)


def test_null_source_is_explicit_http_error(catalog_session, runtime):
    _, baseline = catalog_session
    with baseline.begin():
        baseline.get(ProjectCanvas, CANVAS).source_project_id = None
    request, _ = runtime
    response = request()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "canvas_character_source_required"


def test_unlinked_character_is_explicit_404(runtime):
    request, _ = runtime
    response = request(PATH + "/" + str(ASSET + 100))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("key", ["invalid.key", "a" * 65])
def test_canvas_key_uses_existing_canonical_contract(runtime, key):
    request, _ = runtime
    response = request(PATH.replace(KEY, key))
    assert response.status_code == 422
