"""current 角色冻结和回填的 SQLite 行为边界；非 MySQL/真实模型验收。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from catalog_fixtures import ASSET, CANVAS, KEY
from catalog_fixtures import catalog_session as catalog_session
from sqlalchemy import select

from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    CanvasResult,
    CanvasTaskBinding,
    CanvasToolRun,
    ProjectCanvas,
)
from short_drama.schemas.canvas_generation import CanvasTaskBindOperation
from short_drama.schemas.canvas_tool_runs import CanvasToolRunCreate
from short_drama.service.asset_version_service import AssetVersionService
from short_drama.service.canvas_service import CanvasService
from short_drama.service.canvas_storyboard import canvas_storyboard_result, storyboard_snapshot
from short_drama.service.canvas_task_service import CanvasTaskService
from short_drama.service.canvas_tool_run_service import CanvasToolRunService
from tests.unit.test_canvas_character_publications import (
    REVERSE_OWNING,
    assert_sorted_first_locks,
    reverse_commit,
    reverse_document,
    reverse_owning,
    seed_copy,
    seed_task,
    trace_project_locks,
)
from tests.unit.test_canvas_character_publications import sqlite_mutex_only as sqlite_mutex_only
from tests.unit.test_canvas_storyboard_generation import reply_body, request_body


def source_graph(session):
    source = reverse_document(session)
    source["nodes"].extend(
        [
            {
                "id": "style",
                "type": "text",
                "title": "项目画风 · 电影",
                "position": {"x": 0, "y": 0},
                "width": 320,
                "height": 220,
                "metadata": {
                    "workflowKind": "styleboard",
                    "stylePresetId": "cinematic",
                    "content": "低饱和",
                },
            },
            {
                "id": "script",
                "type": "script",
                "title": "分镜",
                "position": {"x": 320, "y": 0},
                "width": 920,
                "height": 360,
                "metadata": {"taskId": "100", "storyboard": {"rows": []}},
            },
        ]
    )
    return source


def saved_graph(catalog_session):
    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    source = source_graph(session)
    reverse_commit(CanvasService(session), source)
    session.info["request_project"] = REVERSE_OWNING
    return source


def current_document(session):
    with session.begin():
        return CanvasService(session)._document(session.get(ProjectCanvas, CANVAS), private=True)


def frozen_request(source):
    metadata = source["nodes"][0]["metadata"]
    request = request_body()
    request["projectId"] = KEY
    request["input"]["characters"] = [
        {
            "assetId": metadata["characterAssetId"],
            "versionId": metadata["characterVersionId"],
            "name": metadata["characterName"],
            "definition": deepcopy(metadata["characterDefinition"]),
        }
    ]
    request["input"]["canvasAssets"] = [
        {
            "id": "character-one",
            "title": metadata["characterName"],
            "type": "character",
            "tags": [],
            "prompt": metadata["characterPrompt"],
            "characterAssetId": metadata["characterAssetId"],
            "characterVersionId": metadata["characterVersionId"],
        }
    ]
    return request


def role_snapshot(source):
    request = frozen_request(source)
    return {
        "nodeId": "script",
        "rows": [],
        "projectStyle": request["input"]["projectStyle"],
        "characters": request["input"]["characters"],
        "canvasAssets": request["input"]["canvasAssets"],
    }


def seed_storyboard(baseline, source):
    seed_task(baseline)
    request = frozen_request(source)
    reply = reply_body()
    for row in reply["rows"]:
        row["characters"] = [source["nodes"][0]["metadata"]["characterName"]]
    frozen = {"canvas_request": request, "canvas_storyboard_source": role_snapshot(source)}
    with baseline.begin():
        binding = baseline.scalar(select(CanvasTaskBinding))
        binding.node_key = binding.source_node_key = "script"
        binding.source_snapshot = {"request": deepcopy(frozen)}
        record = baseline.scalar(select(AIGenerationRecord))
        record.request_data = frozen
        record.text_content = json.dumps(reply, ensure_ascii=False)


def bind_operation():
    return CanvasTaskBindOperation.model_validate(
        {
            "opId": "r10-current-bind",
            "params": {"canvasId": KEY, "taskId": "100", "nodeId": "script", "outputIndex": 0},
        }
    )


def test_current_role_snapshot_preserves_version_definition_and_node(catalog_session):
    source = saved_graph(catalog_session)
    snapshot = storyboard_snapshot(source, "script")
    role = source["nodes"][0]["metadata"]
    assert snapshot["characters"] == [
        {
            "assetId": str(ASSET),
            "versionId": role["characterVersionId"],
            "name": role["characterName"],
            "definition": role["characterDefinition"],
        }
    ]
    assert snapshot["canvasAssets"] == frozen_request(source)["input"]["canvasAssets"]


def test_same_real_role_duplicate_cards_keep_source_context_and_asset_node_ids(catalog_session):
    source = saved_graph(catalog_session)
    duplicate = deepcopy(source["nodes"][0])
    duplicate["id"] = "character-two"
    source["nodes"].append(duplicate)
    snapshot = storyboard_snapshot(source, "script")
    assert len(snapshot["characters"]) == 2
    assert snapshot["characters"][0] == snapshot["characters"][1]
    assert [item["id"] for item in snapshot["canvasAssets"]] == ["character-one", "character-two"]


@pytest.mark.parametrize("field", ["characterAssetId", "characterVersionId"])
def test_same_name_different_identity_snapshot_rejects_before_model(catalog_session, field):
    source = saved_graph(catalog_session)
    duplicate = deepcopy(source["nodes"][0])
    duplicate["id"] = "character-two"
    duplicate["metadata"][field] = "9200000000000999"
    source["nodes"].append(duplicate)
    with pytest.raises(WorkflowError) as caught:
        storyboard_snapshot(source, "script")
    assert caught.value.code == "canvas_storyboard_character_ambiguous"
    assert caught.value.status_code == 422


@pytest.mark.parametrize("field", ["assetId", "versionId"])
def test_parser_never_silently_maps_same_name_to_other_identity(field):
    request = request_body()
    character = {"assetId": "31", "versionId": "41", "name": "Hero", "definition": {}}
    second = {**character, field: "999"}
    request["input"]["characters"] = [character, second]
    reply = reply_body()
    reply["rows"][0]["characters"] = ["Hero"]
    with pytest.raises(GenerationError, match="invalid_storyboard_result"):
        canvas_storyboard_result(json.dumps(reply), request, "100")


def test_parser_accepts_same_role_duplicate_context_with_stable_output_ids():
    request = request_body()
    character = {"assetId": "31", "versionId": "41", "name": "Hero", "definition": {}}
    request["input"]["characters"] = [character, deepcopy(character)]
    reply = reply_body()
    for row in reply["rows"]:
        row["characters"] = ["Hero"]
    result = canvas_storyboard_result(json.dumps(reply), request, "100")
    assert result == canvas_storyboard_result(json.dumps(reply), request, "100")
    assert result["rows"][0]["characters"] == [
        {"characterName": "Hero", "characterAssetId": "31", "characterVersionId": "41"}
    ]
    assert [row["id"] for row in result["rows"]] == ["shot:100:1", "shot:100:2"]


def tool_create():
    return CanvasToolRunCreate.model_validate(
        {
            "clientKey": "storyboard:r10-create-one",
            "canvasId": KEY,
            "state": {
                "kind": "storyboard",
                "nodeId": "script",
                "sourceNodeId": "script",
                "logicalModelId": "99",
                "model": "fixture-only",
            },
        }
    )


def test_creation_run_uses_real_publication_gate_and_sorted_scope_locks(
    catalog_session, monkeypatch
):
    session, _baseline = catalog_session
    saved_graph(catalog_session)
    locks = trace_project_locks(session)
    monkeypatch.setattr(
        CanvasToolRunService,
        "_model",
        lambda _self, _identifier: (SimpleNamespace(), "openai_chat.v1", "c" * 64),
    )
    result, fresh = CanvasToolRunService(session, SimpleNamespace()).create(tool_create())
    assert fresh
    with session.begin():
        run = session.get(CanvasToolRun, int(result["run"]["id"]))
        assert run.source_snapshot["characters"][0]["assetId"] == str(ASSET)
    assert_sorted_first_locks(locks)


def test_current_body_change_cannot_pass_role_admission_with_old_card(catalog_session, monkeypatch):
    session, baseline = catalog_session
    saved_graph(catalog_session)
    with baseline.begin():
        asset = baseline.get(Asset, ASSET)
        asset.description = "new work"
        asset.row_version += 1
        AssetVersionService(baseline).append_locked(asset)
    calls = []
    monkeypatch.setattr(CanvasToolRunService, "_model", lambda *_args: calls.append("model"))
    with pytest.raises(WorkflowError) as caught:
        CanvasToolRunService(session, SimpleNamespace()).create(tool_create())
    assert caught.value.code == "canvas_character_version_changed"
    assert calls == []


def test_normal_bind_preserves_frozen_role_identity_and_result_is_stable(catalog_session):
    session, baseline = catalog_session
    source = saved_graph(catalog_session)
    seed_storyboard(baseline, source)
    result = CanvasTaskService(session).bind(bind_operation())
    rows = result["result"]["node"]["metadata"]["storyboard"]["rows"]
    assert rows[0]["characters"][0]["characterAssetId"] == str(ASSET)
    assert (
        rows[0]["characters"][0]["characterVersionId"]
        == source["nodes"][0]["metadata"]["characterVersionId"]
    )
    replay = CanvasTaskService(session).bind(bind_operation())
    assert replay["replayed"] is True


@pytest.mark.parametrize("change", ["delete", "refresh", "current_only"])
def test_bind_rejects_frozen_role_change_without_overwrite(catalog_session, change):
    from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService

    session, baseline = catalog_session
    source = saved_graph(catalog_session)
    frozen = deepcopy(role_snapshot(source))
    seed_storyboard(baseline, source)
    if change in {"refresh", "current_only"}:
        with baseline.begin():
            asset = baseline.get(Asset, ASSET)
            asset.description = "new work"
            asset.row_version += 1
            AssetVersionService(baseline).append_locked(asset)
    if change == "delete":
        source["nodes"] = [node for node in source["nodes"] if node["id"] != "character-one"]
        reverse_commit(CanvasService(session), source, expected=8, key="remove-role")
    if change == "refresh":
        item = CanvasCharacterCatalogService(session).get(KEY, ASSET).item
        source["nodes"][0]["metadata"].update(
            characterVersionId=str(item.current_version_id), characterDefinition=item.definition
        )
        reverse_commit(CanvasService(session), source, expected=8, key="refresh-role")
    before = current_document(session)
    locks = trace_project_locks(session)
    with pytest.raises(WorkflowError) as caught:
        CanvasTaskService(session).bind(bind_operation())
    assert caught.value.code == (
        "canvas_character_version_changed"
        if change == "current_only"
        else "canvas_storyboard_source_changed"
    )
    assert current_document(session) == before
    assert_sorted_first_locks(locks)
    with baseline.begin():
        assert baseline.scalar(select(CanvasResult)) is None
        record = baseline.scalar(select(AIGenerationRecord))
        assert record.status == "succeeded"
        assert record.request_data["canvas_storyboard_source"] == frozen


def test_role_context_order_alone_does_not_reject_same_frozen_roles(catalog_session):
    session, baseline = catalog_session
    source = saved_graph(catalog_session)
    seed_storyboard(baseline, source)
    source["nodes"] = list(reversed(source["nodes"]))
    reverse_commit(CanvasService(session), source, expected=8, key="reorder-nodes")
    result = CanvasTaskService(session).bind(bind_operation())
    assert result["result"]["node"]["metadata"]["storyboard"]["rows"]
