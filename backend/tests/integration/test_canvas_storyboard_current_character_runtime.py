"""正常 HTTP/MySQL/MinIO 的 current 角色边界；供应商是受控服务，非真实模型验收。"""

import json
import os
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, CanvasResult, CanvasToolRun
from tests.integration.test_canvas_generation_runtime import TASKS, bind, records
from tests.integration.test_canvas_resource_copy import copy_resource
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_source_project_binding import business, seed_binding
from tests.integration.test_canvas_storyboard_runtime import (
    RUNS,
    approve_and_execute,
    execute,
    prepare_storyboard,
    storyboard_seed,
)
from tests.integration.test_canvas_storyboard_runtime import (
    storyboard_runtime as storyboard_runtime,
)
from tests.integration.test_canvas_text_image_runtime import save_channel
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.unit.test_canvas_storyboard_generation import reply_body

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="显式启用隔离 current 角色 HTTP/MySQL/MinIO 验证",
    ),
]


def read(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def catalog(client, request):
    return read(
        client.get(
            f"/api/v1/canvas-runtime/canvas-projects/{request['projectId']}/character-catalog"
        )
    )["items"]


def create_character(client, source_id):
    asset = read(
        client.post(
            f"/api/v1/projects/{source_id}/assets",
            headers={"Idempotency-Key": "r10-role-" + uuid4().hex},
            json={
                "kind": "character",
                "name": "阿青",
                "description": "青衣少女",
                "prompt": "保持青衣",
            },
        ),
        201,
    )
    candidate = read(
        client.post(
            f"/api/v1/assets/{asset['id']}/image-candidates/upload",
            files={"file": ("r10-character.png", png("purple"), "image/png")},
        ),
        201,
    )
    return read(
        client.post(
            f"/api/v1/assets/{asset['id']}/confirm",
            json={
                "row_version": asset["row_version"],
                "media_id": candidate["media_id"],
                "expected_media_id": None,
            },
        )
    )


def card(item, copy_id, node_id="current-character"):
    return {
        "id": node_id,
        "type": "text",
        "title": item["name"],
        "position": {"x": -400, "y": 0},
        "width": 320,
        "height": 260,
        "metadata": {
            "workflowKind": "character",
            "characterAssetId": item["asset_id"],
            "characterVersionId": item["current_version_id"],
            "characterVersionPolicy": "current",
            "characterName": item["name"],
            "characterAliases": item["definition"].get("aliases", []),
            "characterDefinition": item["definition"],
            "characterPrompt": (
                f"【角色卡：{item['name']}】\n{item['description']}\n{item['prompt']}"
            ),
            "characterCoverUrl": f"/api/v1/canvas-runtime/resources/{copy_id}/file",
        },
    }


def commit(client, path, document):
    current = read(client.get(path + "/my-document"))
    return client.post(
        path + "/commits",
        headers={"Idempotency-Key": "r10-commit-" + uuid4().hex},
        json={"expected_row_version": current["row_version"], "source_document": document},
    )


def update_request(client, path, request):
    document = read(client.get(path + "/my-document"))["source_document"]
    role_nodes = [
        node for node in document["nodes"] if node.get("metadata", {}).get("characterAssetId")
    ]
    request["input"]["characters"] = [
        {
            "assetId": node["metadata"]["characterAssetId"],
            "versionId": node["metadata"]["characterVersionId"],
            "name": node["metadata"]["characterName"],
            "definition": node["metadata"]["characterDefinition"],
        }
        for node in role_nodes
    ]
    request["input"]["canvasAssets"] = [
        {
            "id": node["id"],
            "title": node["title"],
            "type": "character",
            "tags": [],
            "prompt": " ".join(node["metadata"].get("characterPrompt", "").split()),
            "characterAssetId": node["metadata"]["characterAssetId"],
            "characterVersionId": node["metadata"]["characterVersionId"],
        }
        for node in role_nodes
    ]


def character_scope(runtime):
    scope = storyboard_seed(runtime)
    owner, _user, project, path, request = scope
    source = business(owner, "R10 角色来源 " + uuid4().hex)
    # 唯一系统写为新随机 scope 的可信 binding 夹具，不注入 Asset current/version。
    seed_binding(runtime.app, project, source)
    asset = create_character(owner, source["id"])
    item = catalog(owner, request)[0]
    copied = read(
        copy_resource(owner, {"id": asset["media_id"]}, request["projectId"], key=uuid4().hex)
    )["resource"]
    assert copied["id"] != asset["media_id"]
    document = read(owner.get(path + "/my-document"))["source_document"]
    document["nodes"].append(card(item, copied["id"]))
    read(commit(owner, path, document))
    update_request(owner, path, request)
    reply = reply_body()
    for row in reply["rows"]:
        row["characters"] = [item["name"]]
    runtime.state["text"] = json.dumps(reply, ensure_ascii=False)
    return SimpleNamespace(scope=scope, source=source, asset=asset, item=item, copy_id=copied["id"])


def create_and_claim(scope):
    owner, _user, _project, _path, request = scope
    metadata = request["input"]["metadata"]
    run = read(
        owner.post(
            RUNS,
            json={
                "clientKey": metadata["clientOperationId"],
                "canvasId": request["projectId"],
                "state": {
                    "kind": "storyboard",
                    "nodeId": metadata["nodeId"],
                    "sourceNodeId": metadata["sourceNodeId"],
                    "logicalModelId": request["logicalModelId"],
                    "model": request["model"],
                },
            },
        ),
        201,
    )["run"]
    claimed = read(
        owner.post(
            f"{RUNS}/{run['id']}/claim",
            json={"expectedEpoch": run["executionEpoch"], "owner": "r10:owner"},
        )
    )
    return run, {"executionEpoch": claimed["executionEpoch"], "owner": "r10:owner"}


def prepare(scope, run, guard):
    return scope[0].post(
        f"{RUNS}/{run['id']}/submissions/prepare",
        json={**guard, "itemKey": "storyboard", "request": scope[4]},
    )


def approve(scope, run, guard, submission):
    return scope[0].post(
        f"{RUNS}/{run['id']}/submissions/approve",
        json={**guard, "submissionIds": [submission["id"]]},
    )


def patch_current(case):
    owner = case.scope[0]
    asset = read(owner.get(f"/api/v1/assets/{case.asset['id']}"))
    changed = read(
        owner.patch(
            f"/api/v1/assets/{asset['id']}",
            json={
                "row_version": asset["row_version"],
                "description": "青衣少女，新版本 " + uuid4().hex,
            },
        )
    )
    adopted = read(
        owner.post(
            f"/api/v1/assets/{asset['id']}/confirm",
            json={
                "row_version": changed["row_version"],
                "media_id": asset["media_id"],
                "expected_media_id": asset["media_id"],
            },
        )
    )
    assert adopted["state"] == "confirmed"
    item = catalog(owner, case.scope[4])[0]
    assert item["current_version_id"] != case.item["current_version_id"]
    return item


def bind_response(case, task):
    owner, _user, _project, _path, request = case.scope
    return owner.post(
        "/api/v1/canvas-runtime/ops/canvas.task.bind",
        json={
            "opId": "r10-bind-" + uuid4().hex,
            "params": {
                "canvasId": request["projectId"],
                "taskId": task["id"],
                "nodeId": request["input"]["metadata"]["nodeId"],
                "outputIndex": 0,
            },
        },
    )


def test_current_role_normal_frozen_delivery_bind_and_member_privacy(storyboard_runtime):
    runtime = storyboard_runtime
    case = character_scope(runtime)
    owner, user, project, path, request = case.scope
    member, member_user = account(runtime.app, "r10_member_" + uuid4().hex[:8])
    for target in (project, case.source):
        join(runtime.app, owner, member, target["id"], member_user["id"])
    run, guard, submission = prepare_storyboard(case.scope)
    assert runtime.posts == []
    task = approve_and_execute(case.scope, run, guard, submission)
    execute(runtime, task)
    delivered = read(owner.get(f"{TASKS}/{task['id']}"))
    rows = json.loads(delivered["resultJson"])["rows"]
    expected = {
        "characterName": "阿青",
        "characterAssetId": case.asset["id"],
        "characterVersionId": case.item["current_version_id"],
    }
    assert all(row["characters"] == [expected] for row in rows)
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    bound = bind(owner, request, task)["result"]["node"]
    assert bound["metadata"]["storyboard"]["rows"] == rows
    shared = read(member.get(path + "/my-document"))["source_document"]
    assert shared["nodes"][0]["metadata"]["storyboard"]["rows"] == rows
    assert "taskId" not in shared["nodes"][0]["metadata"]
    assert records(runtime.app, user)["tasks"] == 1 and len(runtime.posts) == 1
    with runtime.app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        record = session.scalar(select(AIGenerationRecord))
        frozen = record.request_data["canvas_storyboard_source"]
        assert frozen["characters"] == request["input"]["characters"]
        assert frozen["canvasAssets"] == request["input"]["canvasAssets"]
        assert session.scalar(select(CanvasToolRun)).source_snapshot == frozen


@pytest.mark.parametrize("stage", ["prepare", "approve", "execute"])
def test_current_change_at_each_approval_stage_never_calls_supplier(storyboard_runtime, stage):
    runtime = storyboard_runtime
    case = character_scope(runtime)
    run, guard = create_and_claim(case.scope)
    submission = read(prepare(case.scope, run, guard)) if stage != "prepare" else None
    if stage == "execute":
        read(approve(case.scope, run, guard, submission))
    before = read(case.scope[0].get(case.scope[3] + "/my-document"))
    patch_current(case)
    if stage == "prepare":
        response = prepare(case.scope, run, guard)
    elif stage == "approve":
        response = approve(case.scope, run, guard, submission)
    else:
        response = case.scope[0].post(
            f"{RUNS}/{run['id']}/execute", json={**guard, "submissionId": submission["id"]}
        )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "canvas_character_version_changed"
    assert runtime.posts == [] and records(runtime.app, case.scope[1])["tasks"] == 0
    assert read(case.scope[0].get(case.scope[3] + "/my-document")) == before


@pytest.mark.parametrize("change", ["delete", "refresh", "current_only"])
def test_successful_old_role_task_cannot_bind_after_card_delete_or_version_change(
    storyboard_runtime, change
):
    runtime = storyboard_runtime
    case = character_scope(runtime)
    owner, user, _project, path, _request = case.scope
    run, guard, submission = prepare_storyboard(case.scope)
    task = approve_and_execute(case.scope, run, guard, submission)
    execute(runtime, task)
    delivered = read(owner.get(f"{TASKS}/{task['id']}"))
    assert delivered["status"] == "succeeded"
    item = patch_current(case) if change != "delete" else None
    if change != "current_only":
        document = read(owner.get(path + "/my-document"))["source_document"]
        if change == "delete":
            document["nodes"] = [
                node for node in document["nodes"] if node["id"] != "current-character"
            ]
        else:
            index = next(
                i for i, node in enumerate(document["nodes"]) if node["id"] == "current-character"
            )
            document["nodes"][index] = card(item, case.copy_id)
        read(commit(owner, path, document))
    before = read(owner.get(path + "/my-document"))
    response = bind_response(case, task)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == (
        "canvas_character_version_changed"
        if change == "current_only"
        else "canvas_storyboard_source_changed"
    )
    assert read(owner.get(path + "/my-document")) == before and len(runtime.posts) == 1
    with runtime.app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        assert session.scalar(select(CanvasResult)).attachment_status == "detached"
        record = session.scalar(select(AIGenerationRecord))
        assert record.status == "succeeded" and record.text_content == runtime.state["text"]
        assert (
            record.request_data["canvas_storyboard_source"]["characters"][0]["versionId"]
            == case.item["current_version_id"]
        )


@pytest.mark.parametrize("field", ["definition", "version", "policy"])
def test_forged_role_card_commit_is_rejected_without_supplier(storyboard_runtime, field):
    runtime = storyboard_runtime
    case = character_scope(runtime)
    owner, user, _project, path, _request = case.scope
    before = read(owner.get(path + "/my-document"))
    document = deepcopy(before["source_document"])
    metadata = document["nodes"][-1]["metadata"]
    if field == "definition":
        metadata["characterDefinition"]["forged"] = "different public work"
    elif field == "version":
        metadata["characterVersionId"] = "999"
    else:
        metadata["characterVersionPolicy"] = "pinned"
    rejected = commit(owner, path, document)
    assert rejected.status_code in {409, 422}, rejected.text
    assert read(owner.get(path + "/my-document")) == before
    assert runtime.posts == [] and records(runtime.app, user)["tasks"] == 0


@pytest.mark.parametrize("revoked", ["source", "owning"])
def test_member_scope_revocation_blocks_approval_without_supplier(storyboard_runtime, revoked):
    runtime = storyboard_runtime
    case = character_scope(runtime)
    owner, _user, project, path, original_request = case.scope
    member, member_user = account(runtime.app, "r10_creator_" + uuid4().hex[:8])
    for target in (project, case.source):
        join(runtime.app, owner, member, target["id"], member_user["id"])
    channel = save_channel(runtime, member)
    request = deepcopy(original_request)
    request["logicalModelId"] = channel["modelProfiles"][0]["logicalModelId"]
    request["input"]["metadata"]["clientOperationId"] = "storyboard:r10-member-" + uuid4().hex
    document = read(member.get(path + "/my-document"))["source_document"]
    style = next(node for node in document["nodes"] if node["id"] == "style")
    # stylePresetId 是本人参数；成员须通过正常 commit 保存自己的画风再创作。
    assert "stylePresetId" not in style["metadata"]
    style["metadata"].update(
        stylePresetId=request["input"]["projectStyle"]["presetId"],
        content=request["input"]["projectStyle"]["prompt"],
    )
    read(commit(member, path, document))
    update_request(member, path, request)
    scope = member, member_user, project, path, request
    run, guard, submission = prepare_storyboard(scope)
    target = case.source if revoked == "source" else project
    removed = owner.delete(f"/api/v1/projects/{target['id']}/members/{member_user['id']}")
    assert removed.status_code in {200, 204}, removed.text
    response = approve(scope, run, guard, submission)
    assert response.status_code == 404, response.text
    assert runtime.posts == [] and records(runtime.app, member_user)["tasks"] == 0


@pytest.mark.parametrize("duplicate", ["same_role", "other_same_name"])
def test_duplicate_role_cards_preserve_source_or_reject_identity_ambiguity(
    storyboard_runtime, duplicate
):
    runtime = storyboard_runtime
    case = character_scope(runtime)
    owner, user, _project, path, request = case.scope
    document = read(owner.get(path + "/my-document"))["source_document"]
    if duplicate == "same_role":
        second = deepcopy(document["nodes"][-1])
        second["id"] = "current-character-two"
    else:
        asset = create_character(owner, case.source["id"])
        item = next(item for item in catalog(owner, request) if item["asset_id"] == asset["id"])
        copied = read(
            copy_resource(owner, {"id": asset["media_id"]}, request["projectId"], key=uuid4().hex)
        )["resource"]
        second = card(item, copied["id"], node_id="current-character-two")
    document["nodes"].append(second)
    read(commit(owner, path, document))
    update_request(owner, path, request)
    if duplicate == "same_role":
        run, guard, submission = prepare_storyboard(case.scope)
        task = approve_and_execute(case.scope, run, guard, submission)
        execute(runtime, task)
        assert read(owner.get(f"{TASKS}/{task['id']}"))["status"] == "succeeded"
        assert len(request["input"]["characters"]) == len(request["input"]["canvasAssets"]) == 2
        assert len(runtime.posts) == 1
    else:
        metadata = request["input"]["metadata"]
        rejected = owner.post(
            RUNS,
            json={
                "clientKey": metadata["clientOperationId"],
                "canvasId": request["projectId"],
                "state": {
                    "kind": "storyboard",
                    "nodeId": metadata["nodeId"],
                    "sourceNodeId": metadata["sourceNodeId"],
                    "logicalModelId": request["logicalModelId"],
                    "model": request["model"],
                },
            },
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "canvas_storyboard_character_ambiguous"
        assert runtime.posts == [] and records(runtime.app, user)["tasks"] == 0
