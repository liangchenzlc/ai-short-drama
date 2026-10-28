"""真实登录/CSRF/MySQL 上的只读时间线编译；无供应商与存储替身调用。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskBinding, MediaFile
from short_drama.domain.canvas.document.canvas import CanvasTimeline, CanvasWriteReceipt
from short_drama.domain.canvas.tools.tool_run import CanvasToolRun
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = pytest.mark.integration
PATH = "/api/v1/canvas-runtime/timeline/render-plan"
FIXTURES = Path(__file__).parents[3] / "fixtures" / "canvas_timeline"


def business_counts(identity_app):
    with identity_app[1]() as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                AIGenerationRecord,
                AsyncTask,
                CanvasTaskBinding,
                MediaFile,
                CanvasTimeline,
                CanvasWriteReceipt,
                CanvasToolRun,
            )
        }


@pytest.mark.parametrize("stem", ["gap-mix", "gap-silent-fade", "image-gap-sub", "six-second-mix"])
def test_authenticated_fixed_source_plans_are_repeatable_without_business_writes(
    identity_app, stem
):
    owner, user = account(identity_app, "timeline_plan_owner")
    body = json.loads((FIXTURES / f"{stem}.timeline.json").read_text(encoding="utf-8"))
    expected = json.loads((FIXTURES / f"{stem}.plan.json").read_text(encoding="utf-8"))
    before = business_counts(identity_app)
    for _ in range(2):
        response = owner.post(PATH, json=body, headers={"X-Canvas-Actor": user["id"]})
        assert response.status_code == 200, response.text
        assert response.json() == expected
    assert business_counts(identity_app) == before


def test_real_session_csrf_actor_change_and_anonymous_compile_are_rejected(identity_app):
    owner, user = account(identity_app, "timeline_guard_owner")
    other, _ = account(identity_app, "timeline_guard_other")
    body = json.loads((FIXTURES / "gap-mix.timeline.json").read_text(encoding="utf-8"))
    before = business_counts(identity_app)
    anonymous = TestClient(identity_app[0], headers={"Origin": identity_app[2].public_origin})
    response = anonymous.post(PATH, json=body)
    assert response.status_code == 401, response.text
    response = other.post(PATH, json=body, headers={"X-Canvas-Actor": user["id"]})
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "canvas_actor_changed"
    )
    response = owner.post(PATH, json=body, headers={"X-CSRF-Token": "invalid-fixture-csrf"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_failed"
    assert business_counts(identity_app) == before
