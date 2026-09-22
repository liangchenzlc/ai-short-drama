import json
from contextlib import contextmanager

import pytest
from generation_fixtures import config, generation_session, settings
from pydantic import ValidationError
from sqlalchemy import func, select
from test_episode_writing import setup

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.task_runtime_dao import finish
from short_drama.domain import AIGenerationRecord, Asset, AsyncTask, EpisodeAsset, ProjectAsset
from short_drama.schemas.ai_generation import TextGenerationCreate
from short_drama.schemas.asset_extraction import parse_extraction_result
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.asset_extraction_service import AssetExtractionService
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.generation_business_service import GenerationBusinessService

SCRIPT = "林晚拿起红伞，走进老宅客厅。"
ITEMS = [
    {
        "kind": "character",
        "name": "林晚",
        "aliases": ["小晚"],
        "description": "走进客厅的人物。",
        "prompt": "短剧人物林晚",
        "importance": "core",
        "story_function": "主角进入关键地点并开始行动。",
        "evidence": "林晚拿起红伞",
    },
    {
        "kind": "scene",
        "name": "老宅客厅",
        "description": "老宅内的客厅。",
        "prompt": "老宅客厅，室内场景",
        "importance": "continuity",
        "story_function": "承载主角进入老宅后的连续行动。",
        "scene_time": "",
        "evidence": "老宅客厅",
    },
    {
        "kind": "prop",
        "name": "红伞",
        "description": "林晚拿起的伞。",
        "prompt": "红色雨伞",
        "importance": "core",
        "story_function": "主角主动拿起并带入关键场景的物件。",
        "evidence": "拿起红伞",
    },
]


def body(p, e, sid, version="3", kinds=None):
    return {
        "source": {
            "scene": "script_assets",
            "project_id": str(p),
            "episode_id": str(e),
            "script_id": sid,
            "content_version": version,
        },
        "extraction": {"kinds": kinds or ["character", "scene", "prop"]},
    }


@contextmanager
def extraction(items=None):
    with generation_session() as session:
        config(session, "text")
        p, e, writing = setup(session)
        saved = writing.save_script(
            p, e, {"content_version": "1", "script_id": None, "content": SCRIPT}
        )
        sid = saved["script"]["id"]
        writing.confirm(p, e, sid, {"content_version": "2"})
        task, _ = AIGenerationService(session, settings).create("text", body(p, e, sid), "extract")
        tid = int(task["generation_id"])
        with session.begin():
            record = session.scalar(select(AIGenerationRecord))
            record.text_content = json.dumps(
                {"schema_version": 1, "items": ITEMS if items is None else items}
            )
            record.status = "succeeded"
            record.response_data = {"finish_reason": "stop"}
            GenerationBusinessService(session).save_text_result(tid, record.id)
            finish(session.get(AsyncTask, tid), "succeeded")
        yield session, p, e, writing, sid, tid, AssetExtractionService(session)


def create_request(result, items=None):
    return {
        "result_version": result["result_version"],
        "content_version": result["content_version"],
        "items": items
        or [{"candidate_id": item["candidate_id"], "action": "create"} for item in result["items"]],
    }


def test_extraction_options_are_scoped_unique_and_nonempty():
    parsed = TextGenerationCreate.model_validate(body(1, 2, "3"))
    assert parsed.source.scene == "script_assets"
    for kinds in ([], ["prop", "prop"], ["image"]):
        with pytest.raises(ValidationError):
            TextGenerationCreate.model_validate({**body(1, 2, "3"), "extraction": {"kinds": kinds}})
    with pytest.raises(ValidationError):
        TextGenerationCreate.model_validate(
            {
                "input": {"messages": [{"role": "user", "content": "x"}]},
                "extraction": {"kinds": ["prop"]},
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"prompt": ""},
        {"description": ""},
        {"name": " "},
        {"kind": "video"},
        {"importance": "decorative"},
        {"story_function": " "},
        {"evidence": "虚构的原文"},
        {"media_id": None},
        {"scene_time": "夜"},
        {"tags": ["x" * 41]},
        {"aliases": ["x"] * 21},
    ],
)
def test_invalid_or_invented_extraction_is_rejected(change):
    snapshot = {"content": SCRIPT, "extraction": {"kinds": ["character"]}}
    with pytest.raises(ValueError):
        parse_extraction_result(
            json.dumps({"schema_version": 1, "items": [{**ITEMS[0], **change}]}), snapshot
        )


def test_parser_accepts_empty_and_fenced_json_but_not_wrong_category_or_excess():
    snapshot = {"content": SCRIPT, "extraction": {"kinds": ["prop"]}, "max_candidates": 1}
    raw = json.dumps({"schema_version": 1, "items": [ITEMS[2]]})
    result = parse_extraction_result(f"```json\n{raw}\n```", snapshot)
    assert result["items"][0]["draft"]["prompt"] == "红色雨伞"
    assert result["items"][0]["original"]["importance"] == "core"
    assert result["items"][0]["original"]["story_function"].startswith("主角主动")
    assert "importance" not in result["items"][0]["draft"]
    assert "story_function" not in result["items"][0]["draft"]
    assert parse_extraction_result('{"schema_version":1,"items":[]}', snapshot)["items"] == []
    for raw in (
        "{",
        json.dumps({"schema_version": 1, "items": ITEMS}),
        json.dumps({"schema_version": 1, "items": [ITEMS[0]]}),
    ):
        with pytest.raises(ValueError):
            parse_extraction_result(raw, snapshot)


def test_confirmation_gate_and_snapshot_settings():
    with generation_session() as session:
        config(session, "text")
        p, e, writing = setup(session)
        saved = writing.save_script(
            p, e, {"content_version": "1", "script_id": None, "content": SCRIPT}
        )
        sid = saved["script"]["id"]
        api = AIGenerationService(session, settings)
        with pytest.raises(WorkflowError, match="请先确认"):
            api.create("text", body(p, e, sid, "2"), "unconfirmed")
        writing.confirm(p, e, sid, {"content_version": "2"})
        receipt, _ = api.create("text", body(p, e, sid, kinds=["prop"]), "confirmed")
        with session.begin():
            request = session.scalar(select(AIGenerationRecord)).request_data
            assert request["source_snapshot"]["content"] == SCRIPT
            assert request["source_snapshot"]["extraction"] == {"kinds": ["prop"]}
            assert request["template_version"] == "script-assets-v1-r2"
            system = request["input"]["messages"][0]["content"]
            assert "删除测试" in system
            assert "角色规则" not in system
            assert request["parameters"]["max_output_tokens"] == 8192
        page = api.list(0, 20, {"source_scene": "script_assets", "source_id": sid})
        assert page["items"][0]["generation_id"] == receipt["generation_id"]


def test_candidates_wait_for_adoption_and_partial_apply_is_idempotent_episode_only():
    with extraction() as (session, p, e, _, _, tid, service):
        with session.begin():
            assert session.scalar(select(func.count()).select_from(Asset)) == 0
        result = service.get(p, e, tid)
        request = create_request(
            result, [{"candidate_id": result["items"][0]["candidate_id"], "action": "create"}]
        )
        first = service.apply(p, e, tid, request, "adopt-once")
        replay = service.apply(p, e, tid, request, "adopt-once")
        assert first["created"] == 1 and replay["already_applied"]
        result = service.get(p, e, tid)
        assert result["items"][0]["applied"] and not result["items"][1]["applied"]
        assert service.apply(p, e, tid, create_request(result), "remaining")["created"] == 2
        with session.begin():
            rows = list(session.scalars(select(Asset)))
            assert len(rows) == 3
            assert all(
                row.media_id is None and row.state == "unconfirmed" and row.model_id == 1
                for row in rows
            )
            assert {row.prompt for row in rows} == {item["prompt"] for item in ITEMS}
            assert session.scalar(select(func.count()).select_from(EpisodeAsset)) == 3
            assert session.scalar(select(func.count()).select_from(ProjectAsset)) == 0


def test_edit_persists_all_text_fields_and_rejects_stale_version():
    with extraction() as (_, p, e, _, _, tid, service):
        before = service.get(p, e, tid)
        item = before["items"][0]
        draft = {
            **item["draft"],
            "name": "林晚（雨中）",
            "description": "核对后的描述",
            "prompt": "核对后的提示词",
        }
        patch = {
            "result_version": before["result_version"],
            "items": [{"candidate_id": item["candidate_id"], "draft": draft}],
        }
        updated = service.patch(p, e, tid, patch)
        assert updated["items"][0]["draft"] == draft
        assert updated["items"][0]["original"]["name"] == "林晚"
        with pytest.raises(WorkflowError) as error:
            service.patch(p, e, tid, patch)
        assert error.value.code == "result_version_conflict"
        service.apply(p, e, tid, create_request(updated), "edited")
        assert service.get(p, e, tid)["items"][0]["draft"]["prompt"] == "核对后的提示词"


def test_mixed_reuse_and_creation_never_overwrite_shared_asset():
    with extraction() as (session, p, e, _, _, tid, service):
        library = AssetLibraryService(session)
        existing, _ = library.create(
            "project",
            p,
            p,
            {
                "kind": "character",
                "name": "林晚",
                "description": "已有描述",
                "prompt": "已有提示词",
            },
            "shared",
        )
        result = service.get(p, e, tid)
        item = result["items"][0]
        assert item["matches"][0]["scope"] == "project"
        request = create_request(result)
        request["items"][0].update(
            action="reuse", asset_id=str(existing.id), expected_row_version="1"
        )
        receipt = service.apply(p, e, tid, request, "mixed")
        assert (receipt["created"], receipt["reused"]) == (2, 1)
        current = library.get(existing.id)
        assert (current.description, current.prompt, current.row_version) == (
            "已有描述",
            "已有提示词",
            1,
        )
        assert library.list("episode", e, p)["total"] == 3
        assert library.list("project", p, p)["total"] == 1


def test_duplicate_review_and_failure_rolls_back_entire_batch():
    with extraction() as (session, p, e, _, _, tid, service):
        library = AssetLibraryService(session)
        existing, _ = library.create("episode", e, p, {"kind": "prop", "name": "红伞"}, "existing")
        result = service.get(p, e, tid)
        request = create_request(result)
        with pytest.raises(WorkflowError) as error:
            service.apply(p, e, tid, request, "rollback")
        assert error.value.code == "duplicate_review_required"
        assert library.list("episode", e, p)["total"] == 1
        assert not any(item["applied"] for item in service.get(p, e, tid)["items"])
        request["items"][-1].update(
            action="reuse", asset_id=str(existing.id), expected_row_version="2"
        )
        with pytest.raises(WorkflowError) as error:
            service.apply(p, e, tid, request, "bad-version")
        assert error.value.code == "asset_version_conflict"
        assert library.list("episode", e, p)["total"] == 1


def test_changed_script_blocks_adoption_but_novel_only_change_allows_it():
    with extraction() as (_, p, e, writing, sid, tid, service):
        writing.save_novel(p, e, {"content_version": "3", "content": "小说更新"})
        result = service.get(p, e, tid)
        assert not result["stale"] and result["content_version"] == "4"
        service.apply(
            p,
            e,
            tid,
            create_request(
                result, [{"candidate_id": result["items"][0]["candidate_id"], "action": "create"}]
            ),
            "novel-change",
        )
        writing.save_script(p, e, {"content_version": "4", "script_id": sid, "content": "新的剧本"})
        result = service.get(p, e, tid)
        assert result["stale"]
        with pytest.raises(WorkflowError) as error:
            service.apply(p, e, tid, create_request(result), "stale-source")
        assert error.value.code == "source_changed"


def test_truncated_result_and_wrong_ownership_cannot_be_adopted():
    with extraction() as (session, p, e, _, _, tid, service):
        from short_drama.service.episode_service import EpisodeService

        other = EpisodeService(session).create(
            {"project_id": p, "position": 2, "title": "Other", "aspect": "16:9"}
        )
        with pytest.raises(WorkflowError) as error:
            service.get(p, other.id, tid)
        assert error.value.code == "not_found"
        with session.begin():
            record = session.scalar(select(AIGenerationRecord))
            record.response_data = {"finish_reason": "length"}
            with pytest.raises(WorkflowError) as error:
                GenerationBusinessService(session).save_text_result(tid, record.id)
            assert error.value.code == "text_truncated"
            assert record.text_content


def test_worker_routes_extraction_to_local_save_without_model_or_image_call():
    from sqlalchemy.orm import sessionmaker

    from short_drama.ai.types import GenerationResult
    from short_drama.core.config import Settings
    from short_drama.service.generation_execution_service import GenerationExecutionService

    with extraction() as (session, p, e, _, sid, _, service):
        receipt, _ = AIGenerationService(session, settings).create(
            "text", body(p, e, sid), "worker-extraction"
        )
        tid = int(receipt["generation_id"])
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        worker = GenerationExecutionService(factory, Settings(_env_file=None), object(), None)
        task, record, token = worker.store.claim_execution(tid, 1)
        worker._store_result(
            task,
            record,
            1,
            token,
            GenerationResult(
                status="succeeded",
                adapter="openai_chat",
                text=json.dumps({"schema_version": 1, "items": ITEMS}),
                finish_reason="stop",
            ),
        )
        with factory() as reading:
            task = reading.get(AsyncTask, tid)
            assert task.next_action == "save"
            version = task.message_version
        worker.execute(tid, version)
        worker.execute(tid, version)
        assert len(service.get(p, e, tid)["items"]) == 3
        with session.begin():
            assert session.scalar(select(func.count()).select_from(Asset)) == 0


def test_same_batch_duplicates_require_review_and_can_be_explicitly_created():
    with extraction([ITEMS[0], ITEMS[0]]) as (_, p, e, _, _, tid, service):
        result = service.get(p, e, tid)
        first, second = result["items"]
        assert first["duplicate_candidates"] == [second["candidate_id"]]
        request = create_request(result)
        with pytest.raises(WorkflowError) as error:
            service.apply(p, e, tid, request, "duplicate-batch")
        assert error.value.code == "duplicate_review_required"
        for item in request["items"]:
            item["confirm_duplicate"] = True
        assert service.apply(p, e, tid, request, "explicit-batch")["created"] == 2


def test_apply_key_cannot_be_reused_with_different_selection():
    with extraction() as (_, p, e, _, _, tid, service):
        result = service.get(p, e, tid)
        request = create_request(result)
        service.apply(p, e, tid, request, "same-key")
        request["items"] = request["items"][:1]
        with pytest.raises(WorkflowError) as error:
            service.apply(p, e, tid, request, "same-key")
        assert error.value.code == "idempotency_conflict"


def test_configured_script_bound_rejects_input_without_submitting_a_task():
    from types import SimpleNamespace

    with extraction() as (session, p, e, _, sid, _, _):
        api = AIGenerationService(session, SimpleNamespace(extraction_max_script_chars=5))
        with pytest.raises(WorkflowError) as error:
            api.create("text", body(p, e, sid), "over-limit")
        assert error.value.code == "script_too_long"
        with session.begin():
            assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
