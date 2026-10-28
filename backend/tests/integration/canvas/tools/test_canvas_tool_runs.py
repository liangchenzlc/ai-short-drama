"""审美私人租约、阶段批准、原子任务关联与重放；只用隔离MySQL/MinIO。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from short_drama.core.exceptions import WorkflowError
from short_drama.db.access.policies.canvas_tool_run_access import tool_run_write
from short_drama.domain import AsyncTask, CanvasResult, CanvasToolRun, CanvasToolSubmission
from short_drama.schemas.canvas.generation.tasks.task_runtime import CanvasRuntimeTaskCreate
from short_drama.schemas.canvas.tools.tool_contracts import builtin_tool
from short_drama.service.base import utcnow
from tests.integration.canvas.document.test_canvas_workspace import actor
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, records
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.tools.test_canvas_editor_tools import ART_STAGES, execute, tool_seed
from tests.integration.canvas.tools.test_canvas_editor_tools import tool_runtime as tool_runtime
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

RUNS = "/api/v1/canvas-runtime/creation-runs"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize("tool_runtime", ["chat-completion"], indirect=True),
]


@pytest.fixture
def art_scope(tool_runtime):
    runtime = tool_runtime
    owner, user, project, path, request, image = tool_seed(runtime, image=True)
    initial = owner.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"][0].update(type="ai-art-critique", metadata={})
    key = request["input"]["referenceImages"][0]["storageKey"]
    document["nodes"].append(
        {
            "id": "original-image",
            "type": "image",
            "title": "已有图片",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {
                "storageKey": key,
                "status": "success",
                "mimeType": "image/png",
                "bytes": len(image),
                "naturalWidth": 37,
                "naturalHeight": 19,
            },
        }
    )
    document["connections"] = [
        {"id": "art-input", "fromNodeId": "original-image", "toNodeId": "runtime-node"}
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "art-source-" + uuid4().hex},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    create = {
        "clientKey": "art-critique:control-analysis-one",
        "canvasId": request["projectId"],
        "state": {
            "kind": "art-critique",
            "nodeId": "runtime-node",
            "sourceNodeId": "original-image",
            "sourceFingerprint": "v1-12345678",
            "model": f"host-{request['logicalModelId']}::saved-model",
            "logicalModelId": request["logicalModelId"],
        },
    }
    return runtime, owner, user, project, path, request, create


def create_and_claim(scope, owner_name="plugin:first"):
    _runtime, owner, _user, _project, _path, _request, create = scope
    response = owner.post(RUNS, json=create)
    assert response.status_code == 201, response.text
    run = response.json()["run"]
    claimed = owner.post(
        f"{RUNS}/{run['id']}/claim",
        json={"expectedEpoch": run["executionEpoch"], "owner": owner_name},
    )
    assert claimed.status_code == 200, claimed.text
    guard = {"owner": owner_name, "executionEpoch": claimed.json()["executionEpoch"]}
    return claimed.json(), guard


def stage_request(scope, run, name="review_art_composition"):
    request = deepcopy(scope[5])
    canonical = request["input"]["agentRequests"]["canonical"]
    canonical["tools"] = [builtin_tool(name)]
    canonical["toolChoice"]["name"] = name
    request["input"]["metadata"].update(
        source="art-critique",
        stage=name,
        runId="control-analysis-one",
        clientOperationId=f"canvas-tool:{run['id']}:{name}",
    )
    return request


def prepare(scope, run, guard, *, name="review_art_composition", request=None):
    response = scope[1].post(
        f"{RUNS}/{run['id']}/submissions/prepare",
        json={
            **guard,
            "itemKey": "stage:" + name,
            "request": request or stage_request(scope, run, name),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def approve(scope, run, guard, submission):
    response = scope[1].post(
        f"{RUNS}/{run['id']}/submissions/approve",
        json={**guard, "submissionIds": [submission["id"]]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["submissions"][0]["approvedAt"]


def test_run_create_is_private_idempotent_and_does_not_submit_a_model(art_scope):
    runtime, owner, user, _project, path, _request, create = art_scope
    before = owner.get(path + "/my-document").json()
    run, _guard = create_and_claim(art_scope)
    repeated = owner.post(RUNS, json=create)
    assert repeated.status_code == 200 and repeated.json()["run"]["id"] == run["id"]
    changed = deepcopy(create)
    changed["state"]["sourceFingerprint"] = "v1-87654321"
    assert owner.post(RUNS, json=changed).status_code == 409
    assert run["revision"] == "1" and run["executionEpoch"] == "1"
    assert run["canvasId"] == create["canvasId"] and run["userId"] == user["id"]
    assert owner.get(path + "/my-document").json() == before
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert runtime.posts == []


def test_lease_conflict_expiry_and_release_cannot_resurrect_an_old_owner(art_scope):
    runtime, owner = art_scope[:2]
    run, guard = create_and_claim(art_scope)
    other = {"owner": "plugin:second", "expectedEpoch": run["executionEpoch"]}
    assert owner.post(f"{RUNS}/{run['id']}/claim", json=other).status_code == 409
    assert owner.post(f"{RUNS}/{run['id']}/heartbeat", json=guard).status_code == 200
    with runtime.app[1]() as session, session.begin():
        session.get(CanvasToolRun, int(run["id"])).lease_expires_at = utcnow() - timedelta(
            seconds=1
        )
    assert owner.post(f"{RUNS}/{run['id']}/heartbeat", json=guard).status_code == 409
    claimed = owner.post(f"{RUNS}/{run['id']}/claim", json=other)
    assert claimed.status_code == 200 and claimed.json()["executionEpoch"] == "2"
    assert owner.post(f"{RUNS}/{run['id']}/release", json=guard).status_code == 409
    current = {"owner": "plugin:second", "executionEpoch": "2"}
    assert owner.post(f"{RUNS}/{run['id']}/release", json=current).json() == {"released": True}
    assert owner.post(f"{RUNS}/{run['id']}/heartbeat", json=current).status_code == 409


def test_approved_stage_admission_and_concurrent_replay_link_one_task_atomically(art_scope):
    runtime, owner, user, _project, path = art_scope[:5]
    before = owner.get(path + "/my-document").json()
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    endpoint = f"{RUNS}/{run['id']}/execute"
    command = {**guard, "submissionId": submission["id"]}
    assert owner.post(endpoint, json=command).status_code == 409
    approve(art_scope, run, guard, submission)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: owner.post(endpoint, json=command), range(2)))
    assert sorted(item.status_code for item in responses) == [200, 202], [
        item.text for item in responses
    ]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    detail = owner.get(f"{RUNS}/{run['id']}").json()
    assert detail["submissions"][0]["taskId"] == responses[0].json()["id"]
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert owner.get(path + "/my-document").json() == before and runtime.posts == []
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0


def test_stage_request_hash_and_fixed_scope_prevent_replacement_or_extra_charges(art_scope):
    run, guard = create_and_claim(art_scope)
    original = prepare(art_scope, run, guard)
    assert prepare(art_scope, run, guard)["id"] == original["id"]
    request = stage_request(art_scope, run)
    request["prompt"] = request["input"]["prompt"] = "改变收费输入"
    refused = art_scope[1].post(
        f"{RUNS}/{run['id']}/submissions/prepare",
        json={**guard, "itemKey": "stage:review_art_composition", "request": request},
    )
    assert refused.status_code == 409, refused.text
    request = stage_request(art_scope, run)
    request["input"]["metadata"]["clientOperationId"] = "different-operation-identity"
    assert (
        art_scope[1]
        .post(
            f"{RUNS}/{run['id']}/submissions/prepare",
            json={**guard, "itemKey": "extra-stage", "request": request},
        )
        .status_code
        == 422
    )
    assert art_scope[1].post(TASKS, json=stage_request(art_scope, run)).status_code == 409
    assert art_scope[0].posts == []


@pytest.mark.parametrize("change", ["image", "connection", "model"])
def test_unexecuted_stage_rechecks_source_model_and_activation(art_scope, change):
    runtime, owner, user, _project, path, _request, _create = art_scope
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    approve(art_scope, run, guard, submission)
    if change in {"image", "connection"}:
        initial = owner.get(path + "/my-document").json()
        document = initial["source_document"]
        if change == "image":
            document["nodes"][1]["metadata"]["naturalWidth"] = 38
        else:
            document["connections"] = []
        response = owner.post(
            path + "/commits",
            headers={"Idempotency-Key": "change-source-" + uuid4().hex},
            json={"expected_row_version": initial["row_version"], "source_document": document},
        )
        assert response.status_code == 200, response.text
    else:
        from short_drama.domain import AIModelConfig

        with runtime.app[1]() as session, session.begin():
            model = session.get(AIModelConfig, int(art_scope[5]["logicalModelId"]))
            model.row_version += 1
            model.base_url += "/changed"
    response = owner.post(
        f"{RUNS}/{run['id']}/execute", json={**guard, "submissionId": submission["id"]}
    )
    assert response.status_code == 409, response.text
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert runtime.posts == []


def test_task_receipt_survives_release_and_an_expired_observation(art_scope):
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    approve(art_scope, run, guard, submission)
    owner = art_scope[1]
    task = owner.post(
        f"{RUNS}/{run['id']}/execute", json={**guard, "submissionId": submission["id"]}
    )
    assert task.status_code == 202, task.text
    assert owner.post(f"{RUNS}/{run['id']}/release", json=guard).status_code == 200
    claimed = owner.post(
        f"{RUNS}/{run['id']}/claim",
        json={"expectedEpoch": "1", "owner": "plugin:resumed"},
    )
    assert claimed.status_code == 200, claimed.text
    current = {"executionEpoch": "2", "owner": "plugin:resumed"}
    assert prepare(art_scope, run, current)["taskId"] == task.json()["id"]
    replay = owner.post(
        f"{RUNS}/{run['id']}/execute", json={**current, "submissionId": submission["id"]}
    )
    assert replay.status_code == 200 and replay.json()["id"] == task.json()["id"]
    assert records(art_scope[0].app, art_scope[2]) == {"tasks": 1, "records": 1, "bindings": 1}


def test_run_submission_and_lease_are_author_private_even_in_a_shared_project(art_scope):
    runtime, owner, _user, project, path = art_scope[:5]
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    member, member_user = account(runtime.app, "art_run_member")
    assert member.get(path).status_code == 404
    assert member.get(RUNS).json() == {"runs": []}
    for endpoint, method, payload in (
        ("", "get", None),
        ("/claim", "post", {"expectedEpoch": "1", "owner": "plugin:member"}),
        ("/heartbeat", "post", guard),
        ("/release", "post", guard),
        ("/submissions/approve", "post", {**guard, "submissionIds": [submission["id"]]}),
        ("/execute", "post", {**guard, "submissionId": submission["id"]}),
        (
            "/submissions/prepare",
            "post",
            {
                **guard,
                "itemKey": "stage:review_art_composition",
                "request": stage_request(art_scope, run),
            },
        ),
    ):
        response = member.request(method, f"{RUNS}/{run['id']}{endpoint}", json=payload)
        assert response.status_code == 404, response.text
    owner.headers["X-Canvas-Actor"] = member_user["id"]
    assert owner.get(f"{RUNS}/{run['id']}").status_code == 409


@pytest.mark.parametrize(
    "field", ["approved_at", "request_hash", "async_task_id", "delete", "bulk"]
)
def test_ordinary_actor_session_cannot_forge_approval_or_rewrite_a_receipt(art_scope, field):
    runtime, owner, user = art_scope[:3]
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    with runtime.app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        session.info["canvas_tool_authority"] = True
        with pytest.raises(WorkflowError) as rejected, session.begin():
            item = session.get(CanvasToolSubmission, int(submission["id"]))
            if field == "delete":
                session.delete(item)
            elif field == "bulk":
                session.execute(update(CanvasToolSubmission).values(approved_at=utcnow()))
            else:
                setattr(
                    item,
                    field,
                    utcnow()
                    if field == "approved_at"
                    else 1
                    if field == "async_task_id"
                    else "0" * 64,
                )
            session.flush()
        assert rejected.value.code in {
            "canvas_tool_authority_required",
            "guarded_canvas_bulk_write",
        }
    detail = owner.get(f"{RUNS}/{run['id']}").json()
    assert detail["submissions"] == [submission]
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}


@pytest.mark.parametrize("field", ["approved_at", "async_task_id", "request_hash"])
def test_service_authority_still_cannot_replace_a_persisted_receipt(art_scope, field):
    runtime, owner, user = art_scope[:3]
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    approve(art_scope, run, guard, submission)
    response = owner.post(
        f"{RUNS}/{run['id']}/execute", json={**guard, "submissionId": submission["id"]}
    )
    assert response.status_code == 202, response.text
    before = owner.get(f"{RUNS}/{run['id']}").json()
    with runtime.app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        with pytest.raises(WorkflowError) as rejected, tool_run_write(session), session.begin():
            item = session.get(CanvasToolSubmission, int(submission["id"]))
            setattr(item, field, "0" * 64 if field == "request_hash" else None)
            session.flush()
        assert rejected.value.code in {
            "canvas_tool_request_immutable",
            "canvas_tool_receipt_immutable",
        }
    assert owner.get(f"{RUNS}/{run['id']}").json() == before


def test_in_memory_approval_without_service_authority_cannot_admit_a_task(art_scope):
    from short_drama.service.canvas.generation.generation import CanvasGenerationService

    runtime, _owner, user = art_scope[:3]
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    with runtime.app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        with pytest.raises(WorkflowError, match="可信批准"), session.begin():
            item = session.get(CanvasToolSubmission, int(submission["id"]))
            item.approved_at = utcnow()
            CanvasGenerationService(session, runtime.app[2]).create_locked(
                CanvasRuntimeTaskCreate.model_validate(item.request_json), tool_submission=item
            )
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}


def test_only_the_nine_fixed_stages_can_be_admitted_once_each(art_scope):
    runtime, owner, user = art_scope[:3]
    run, guard = create_and_claim(art_scope)
    submissions = [prepare(art_scope, run, guard, name=name) for name in ART_STAGES]
    approval = owner.post(
        f"{RUNS}/{run['id']}/submissions/approve",
        json={**guard, "submissionIds": [item["id"] for item in submissions]},
    )
    assert approval.status_code == 200, approval.text
    ids = []
    for item in submissions:
        response = owner.post(
            f"{RUNS}/{run['id']}/execute", json={**guard, "submissionId": item["id"]}
        )
        assert response.status_code == 202, response.text
        ids.append(response.json()["id"])
    assert len(set(ids)) == 9
    assert records(runtime.app, user) == {"tasks": 9, "records": 9, "bindings": 9}
    assert len(owner.get(f"{RUNS}/{run['id']}").json()["submissions"]) == 9
    request = stage_request(art_scope, run, "optimize_prompt")
    response = owner.post(
        f"{RUNS}/{run['id']}/submissions/prepare",
        json={**guard, "itemKey": "stage:optimize_prompt", "request": request},
    )
    assert response.status_code == 422, response.text
    assert runtime.posts == []


def test_unknown_stage_receipt_is_observed_without_resubmitting_the_supplier(art_scope):
    runtime, owner = art_scope[:2]
    run, guard = create_and_claim(art_scope)
    submission = prepare(art_scope, run, guard)
    approve(art_scope, run, guard, submission)
    command = {**guard, "submissionId": submission["id"]}
    task = owner.post(f"{RUNS}/{run['id']}/execute", json=command).json()
    runtime.state["mode"] = "disconnect"
    execute(runtime, task)
    failed = owner.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and not failed["canRetry"] and not failed["canResume"]
    assert owner.post(f"{RUNS}/{run['id']}/release", json=guard).status_code == 200
    claimed = owner.post(
        f"{RUNS}/{run['id']}/claim", json={"expectedEpoch": "1", "owner": "plugin:restored"}
    )
    assert claimed.status_code == 200, claimed.text
    current = {"owner": "plugin:restored", "executionEpoch": "2"}
    restored = prepare(art_scope, run, current)
    assert restored["taskId"] == task["id"]
    replay = owner.post(
        f"{RUNS}/{run['id']}/execute", json={**current, "submissionId": restored["id"]}
    )
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    execute(runtime, task)
    assert len(runtime.posts) == 1


def test_legacy_proposal_and_shared_canvas_writes_are_not_enabled_by_tool_runs(art_scope):
    run, guard = create_and_claim(art_scope)
    for suffix in ("proposal-approve", "canvas", "canvas-commit"):
        assert art_scope[1].post(f"{RUNS}/{run['id']}/{suffix}", json=guard).status_code == 404
    with art_scope[0].app[1]() as session:
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
