"""九个持久批准阶段的四协议真实Worker/HTTP/MySQL/MinIO，不调用收费模型。"""

import json

import pytest
from sqlalchemy import func, select

from short_drama.domain import AIGenerationRecord, CanvasResult
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, records
from tests.integration.canvas.resources.test_canvas_resources import png
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.tools.test_canvas_editor_tools import (
    ART_STAGES,
    art_arguments,
    execute,
)
from tests.integration.canvas.tools.test_canvas_editor_tools import tool_runtime as tool_runtime
from tests.integration.canvas.tools.test_canvas_tool_runs import (
    RUNS,
    approve,
    create_and_claim,
    prepare,
)
from tests.integration.canvas.tools.test_canvas_tool_runs import art_scope as art_scope
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = pytest.mark.integration


def test_approved_art_stages_run_worker_protocols_without_materializing_work(art_scope):
    runtime, owner, user, _project, path = art_scope[:5]
    before = owner.get(path + "/my-document").json()
    run, guard = create_and_claim(art_scope)
    for name in ART_STAGES:
        assert owner.post(f"{RUNS}/{run['id']}/heartbeat", json=guard).status_code == 200
        submission = prepare(art_scope, run, guard, name=name)
        approve(art_scope, run, guard, submission)
        task = owner.post(
            f"{RUNS}/{run['id']}/execute",
            json={**guard, "submissionId": submission["id"]},
        )
        assert task.status_code == 202, task.text
        execute(runtime, task.json())
        completed = owner.get(f"{TASKS}/{task.json()['id']}").json()
        assert completed["status"] == "succeeded", completed
        result = json.loads(completed["resultJson"])
        assert completed["outputs"] == [] and result["text"] == ""
        assert result["toolCalls"][0]["function"]["name"] == name
        assert json.loads(result["toolCalls"][0]["function"]["arguments"]) == art_arguments(name)
    assert [item["name"] for item in runtime.posts] == list(ART_STAGES)
    assert owner.get(path + "/my-document").json() == before
    assert all(item["images"] == [png("red")] for item in runtime.posts)
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        calls = list(session.scalars(select(AIGenerationRecord)))
        assert len(calls) == 9
        assert all(call.request_data["canvas_tool_run_id"] == run["id"] for call in calls)
    assert records(runtime.app, user) == {"tasks": 9, "records": 9, "bindings": 9}
