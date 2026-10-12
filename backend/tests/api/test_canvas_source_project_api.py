"""本地ASGI+SQLite合同；不启动API、不调用业务库或供应商。"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from short_drama.api.dependencies import get_session
from short_drama.api.v1.canvas_source_project import router
from short_drama.core.exceptions import BusinessError
from short_drama.domain import Episode, EpisodeNovel, ProjectCanvas
from short_drama.domain.collaboration import ProjectMember
from short_drama.service.base import utcnow
from tests.catalog_fixtures import CANVAS, KEY, OWNING, SOURCE, actor

pytest_plugins = ("tests.catalog_fixtures",)

EPISODE = 9600000000000001
PATH = f"/api/v1/canvas-runtime/canvas-projects/{KEY}/source-project"
PARAMS = {
    "expected_canvas_row_version": "7",
    "expected_owning_project_id": str(OWNING),
    "expected_source_project_id": str(SOURCE),
}
RAW = " <标签样文本> &amp;\r\n原文  "


@pytest.fixture
def runtime(catalog_session):
    session, baseline = catalog_session
    now = utcnow()
    with baseline.begin():
        baseline.add(
            Episode(
                id=EPISODE,
                project_id=SOURCE,
                position=1,
                title="真实分集",
                synopsis="",
                aspect="16:9",
                style="",
                row_version=1,
                content_version=5,
                storyboard_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.add(EpisodeNovel(id=EPISODE + 20, episode_id=EPISODE, content=RAW))
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

    return request, session, baseline


def test_overview_and_body_paths_have_exact_string_context_and_plain_original(runtime):
    request, _, _ = runtime
    overview = request(params=PARAMS, headers={"X-Canvas-Actor": "1"})
    assert overview.status_code == 200, overview.text
    read = overview.json()
    assert read["owning_project_id"] == str(OWNING) and read["source_project_id"] == str(SOURCE)
    assert read["episodes"][0]["id"] == str(EPISODE)
    body = request(
        PATH + f"/episodes/{EPISODE}", params={**PARAMS, "expected_content_version": "5"}
    )
    assert body.status_code == 200, body.text
    assert body.json()["novel"] == {
        "state": "present",
        "content_format": "plain_text",
        "plain_text": RAW,
    }
    assert body.json()["episode"]["content_version"] == "5"


@pytest.mark.parametrize("field", list(PARAMS))
def test_each_expected_context_parameter_is_required(runtime, field):
    request, _, _ = runtime
    response = request(params={key: value for key, value in PARAMS.items() if key != field})
    assert response.status_code == 422


@pytest.mark.parametrize("value", ["0", "-1", "1.2", "18446744073709551616"])
def test_invalid_version_rejected_before_source_read(runtime, value):
    request, _, _ = runtime
    assert request(params={**PARAMS, "expected_canvas_row_version": value}).status_code == 422


def test_body_content_version_required_and_stale_is_409(runtime):
    request, _, _ = runtime
    path = PATH + f"/episodes/{EPISODE}"
    assert request(path, params=PARAMS).status_code == 422
    stale = request(path, params={**PARAMS, "expected_content_version": "4"})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "canvas_source_content_conflict"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("expected_canvas_row_version", "6", "canvas_revision_conflict"),
        ("expected_source_project_id", "999", "canvas_source_context_conflict"),
        ("expected_owning_project_id", "999", "canvas_source_context_conflict"),
    ],
)
def test_expected_values_cannot_select_unbound_context(runtime, field, value, code):
    request, _, _ = runtime
    response = request(params={**PARAMS, field: value, "source_project_id": "999"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == code


def test_actor_header_switch_and_anonymous_do_not_open_system_session(runtime):
    request, session, _ = runtime
    switched = request(params=PARAMS, headers={"X-Canvas-Actor": "2"})
    assert (
        switched.status_code == 409 and switched.json()["error"]["code"] == "canvas_actor_changed"
    )
    session.info.pop("actor")
    anonymous = request(params={**PARAMS, "system": "true"})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize("project_id", [OWNING, SOURCE])
def test_member_losing_either_permission_gets_404_despite_expected_context(runtime, project_id):
    request, session, baseline = runtime
    session.info["actor"] = actor(2)
    with baseline.begin():
        baseline.scalar(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        ).status = "removed"
    response = request(params={**PARAMS, "system": "true"}, headers={"X-Canvas-Actor": "2"})
    assert response.status_code == 404


def test_missing_binding_is_explicit_409_and_unknown_episode_is_404(runtime):
    request, _, baseline = runtime
    unknown = request(
        PATH + f"/episodes/{EPISODE + 1}", params={**PARAMS, "expected_content_version": "5"}
    )
    assert unknown.status_code == 404
    with baseline.begin():
        baseline.get(ProjectCanvas, CANVAS).source_project_id = None
    missing = request(params=PARAMS)
    assert (
        missing.status_code == 409
        and missing.json()["error"]["code"] == "canvas_source_project_required"
    )
