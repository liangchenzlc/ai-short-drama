import json

import pytest
from pydantic import ValidationError

from short_drama.schemas.ai_generation import ImageGenerationCreate, TextGenerationCreate


def test_business_text_accepts_saved_source_without_client_messages():
    parsed = TextGenerationCreate.model_validate(
        {
            "source": {
                "scene": "novel_script",
                "project_id": "11",
                "episode_id": "12",
                "content_version": "3",
            },
            "instructions": "Keep dialogue",
        }
    )
    assert parsed.source.episode_id == 12
    assert parsed.input is None
    with pytest.raises(ValidationError):
        TextGenerationCreate.model_validate(
            {
                **parsed.model_dump(mode="json"),
                "input": {"messages": [{"role": "user", "content": "wrong source"}]},
            }
        )


def test_storyboard_options_default_validate_bounds_and_belong_only_to_script_shots():
    source = {
        "scene": "script_shots",
        "project_id": "1",
        "episode_id": "2",
        "script_id": "3",
        "content_version": "4",
    }
    parsed = TextGenerationCreate.model_validate({"source": source})
    assert parsed.storyboard.average_shot_duration_ms == 3000
    assert (
        TextGenerationCreate.model_validate(
            {"source": source, "storyboard": {"average_shot_duration_ms": 5000}}
        ).storyboard.average_shot_duration_ms
        == 5000
    )
    for invalid in (999, 10001, 3000.5):
        with pytest.raises(ValidationError):
            TextGenerationCreate.model_validate(
                {"source": source, "storyboard": {"average_shot_duration_ms": invalid}}
            )
    with pytest.raises(ValidationError):
        TextGenerationCreate.model_validate(
            {
                "source": {**source, "scene": "script_assets"},
                "storyboard": {"average_shot_duration_ms": 5000},
            }
        )


def test_omitted_and_explicit_default_storyboard_options_have_the_same_idempotency_intent():
    source = {
        "scene": "script_shots",
        "project_id": "1",
        "episode_id": "2",
        "script_id": "3",
        "content_version": "4",
    }
    omitted = TextGenerationCreate.model_validate({"source": source})
    explicit = TextGenerationCreate.model_validate(
        {"source": source, "storyboard": {"average_shot_duration_ms": 3000}}
    )

    assert omitted.model_dump(mode="json", exclude_unset=True) == explicit.model_dump(
        mode="json", exclude_unset=True
    )


def test_generic_text_still_requires_messages_and_forbids_business_instructions():
    with pytest.raises(ValidationError):
        TextGenerationCreate.model_validate({})
    with pytest.raises(ValidationError):
        TextGenerationCreate.model_validate(
            {
                "input": {"messages": [{"role": "user", "content": "hello"}]},
                "instructions": "override",
            }
        )


def test_saved_image_allows_empty_supplement_but_requires_context_tokens():
    body = {
        "input": {"prompt": ""},
        "source": {
            "scene": "shot_image",
            "shot_id": "11",
            "layout": "single",
            "context_mode": "saved",
            "row_version": "2",
            "context_hash": "a" * 64,
        },
    }
    assert ImageGenerationCreate.model_validate(body).input.prompt == ""
    del body["source"]["row_version"]
    with pytest.raises(ValidationError):
        ImageGenerationCreate.model_validate(body)
    with pytest.raises(ValidationError):
        ImageGenerationCreate.model_validate({"input": {"prompt": ""}})


def test_structured_shots_accepts_one_fence_and_rejects_unknown_asset_or_extra_fields():
    from short_drama.schemas.storyboard_result import parse_storyboard_result

    source = "林晚走进客厅。她拿起桌上的证据。"
    shot = {
        "title": "进入客厅",
        "source_excerpt": "林晚走进客厅。",
        "story_beat": "主角进入关键地点。",
        "script": "Wide shot",
        "duration_ms": 3000,
        "asset_ids": ["11"],
    }
    text = json.dumps({"shots": [shot]})
    assert parse_storyboard_result("```json\n" + text + "\n```", {11}, source) == {"shots": [shot]}
    with pytest.raises(ValueError):
        parse_storyboard_result(text, {12}, source)
    with pytest.raises(ValueError):
        parse_storyboard_result(
            json.dumps({"shots": [{**shot, "asset_ids": [], "id": "3"}]}), set(), source
        )


def test_structured_shots_reject_unverified_or_reversed_source_and_invalid_duration():
    from short_drama.schemas.storyboard_result import parse_storyboard_result

    source = "林晚走进客厅。她拿起桌上的证据。"
    base = {
        "title": "节拍",
        "story_beat": "剧情发生变化。",
        "script": "镜头脚本",
        "duration_ms": 3000,
        "asset_ids": [],
    }
    invalid_batches = [
        [{**base, "source_excerpt": "不存在的原文"}],
        [
            {**base, "source_excerpt": "她拿起桌上的证据。"},
            {**base, "source_excerpt": "林晚走进客厅。"},
        ],
        [{**base, "source_excerpt": "林晚走进客厅。", "duration_ms": 999}],
        [{**base, "source_excerpt": "林晚走进客厅。", "duration_ms": 10001}],
    ]
    for shots in invalid_batches:
        with pytest.raises(ValueError):
            parse_storyboard_result(json.dumps({"shots": shots}), set(), source)


@pytest.mark.parametrize(
    "shots",
    [
        [],
        [
            {
                "title": "x",
                "source_excerpt": "source",
                "story_beat": "beat",
                "script": "",
                "duration_ms": 3000,
                "asset_ids": [],
            }
        ],
        [
            {
                "title": "x",
                "source_excerpt": "source",
                "story_beat": "beat",
                "script": "x",
                "duration_ms": 3000,
                "asset_ids": [],
            }
        ]
        * 101,
        [
            {
                "title": "x",
                "source_excerpt": "source",
                "story_beat": "beat",
                "script": "x",
                "duration_ms": 3000,
                "asset_ids": ["11", "11"],
            }
        ],
    ],
)
def test_structured_shots_rejects_empty_oversize_and_duplicate_refs(shots):
    from short_drama.schemas.storyboard_result import parse_storyboard_result

    with pytest.raises(ValueError):
        parse_storyboard_result(json.dumps({"shots": shots}), {11}, "source")


def test_novel_snapshot_replay_and_candidate_do_not_replace_editor():
    from generation_fixtures import config, generation_session, settings
    from sqlalchemy import select
    from test_episode_writing import setup

    from short_drama.domain import AIGenerationRecord
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.generation_business_service import GenerationBusinessService

    with generation_session() as session:
        config(session, "text")
        p, e, writing = setup(session)
        writing.save_novel(p, e, {"content_version": "1", "content": "old novel"})
        api = AIGenerationService(session, settings)
        body = {
            "source": {
                "scene": "novel_script",
                "project_id": str(p),
                "episode_id": str(e),
                "content_version": "2",
            }
        }
        task, created = api.create("text", body, "novel-once")
        writing.save_novel(p, e, {"content_version": "2", "content": "new novel"})
        replay, created_again = api.create("text", body, "novel-once")
        assert created and not created_again
        assert task["generation_id"] == replay["generation_id"]
        with session.begin():
            record = session.scalar(select(AIGenerationRecord))
            assert record.request_data["source_snapshot"]["content"] == "old novel"
            record.text_content = "Generated script"
            record.response_data = {"finish_reason": "stop"}
            service = GenerationBusinessService(session)
            first = service.save_text_result(record.task_id, record.id)
            assert service.save_text_result(record.task_id, record.id) == first
        state = writing.get(p, e)
        assert state["novel"]["content"] == "new novel"
        assert state["content_version"] == "3"
        assert state["editing_script"] is None
        candidates = writing.candidates(p, e)
        assert candidates["total"] == 1
        assert candidates["items"][0]["generation_id"] == task["generation_id"]


def test_truncated_text_cannot_be_resumed_as_success():
    from types import SimpleNamespace

    from short_drama.service.ai_generation_service import resume_action

    task = SimpleNamespace(
        status="failed", locked_until=None, error={"code": "text_truncated"}, next_action=None
    )
    record = SimpleNamespace(
        status="succeeded", text_content="incomplete", response_data={"finish_reason": "length"}
    )
    assert resume_action(task, record) is None


def test_image_adoption_requires_editor_and_context_tokens_but_video_contract_unchanged():
    from short_drama.schemas.media_asset import MediaAssetApply

    with pytest.raises(ValidationError):
        MediaAssetApply.model_validate(
            {"target": {"type": "shot_image", "id": "11"}, "expected_media_id": None}
        )
    parsed = MediaAssetApply.model_validate(
        {
            "target": {"type": "shot_image", "id": "11"},
            "expected_media_id": None,
            "expected_row_version": "2",
            "expected_context_hash": "a" * 64,
        }
    )
    assert parsed.expected_row_version == 2
    assert not parsed.acknowledge_stale_source
    assert (
        MediaAssetApply.model_validate(
            {"target": {"type": "shot_video", "id": "11"}, "expected_media_id": None}
        ).target.type
        == "shot_video"
    )


@pytest.mark.parametrize("mode", ["append", "replace"])
def test_storyboard_result_waits_for_adoption_replays_once_and_preserves_archived_history(mode):
    from generation_fixtures import config, generation_session, settings
    from sqlalchemy import select
    from test_episode_writing import setup

    from short_drama.dao.task_runtime_dao import finish
    from short_drama.domain import AIGenerationRecord, AsyncTask, ScriptShotRecord, ShotScript
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
    from short_drama.service.generation_business_service import GenerationBusinessService

    with generation_session() as session:
        config(session, "text")
        p, e, writing = setup(session)
        saved = writing.save_script(
            p, e, {"content_version": "1", "script_id": None, "content": "script"}
        )
        sid = saved["script"]["id"]
        writing.confirm(p, e, sid, {"content_version": "2"})
        board = EpisodeStoryboardService(session)
        old = board.create(p, e, {"storyboard_version": "1", "script": "old shot"}, "old-shot")
        task, _ = AIGenerationService(session, settings).create(
            "text",
            {
                "source": {
                    "scene": "script_shots",
                    "project_id": str(p),
                    "episode_id": str(e),
                    "script_id": sid,
                    "content_version": "3",
                },
                "storyboard": {"average_shot_duration_ms": 5000},
            },
            "split-script",
        )
        tid = int(task["generation_id"])
        with session.begin():
            record = session.scalar(select(AIGenerationRecord))
            request = record.request_data
            assert request["template_version"] == "script-shots-v1-r3"
            assert request["source_snapshot"]["storyboard"] == {"average_shot_duration_ms": 5000}
            user_envelope = json.loads(request["input"]["messages"][1]["content"])
            assert user_envelope["source"]["storyboard"]["average_shot_duration_ms"] == 5000
            assert "平均镜头时长" in request["input"]["messages"][0]["content"]
            record.text_content = json.dumps(
                {
                    "shots": [
                        {
                            "title": "New shot",
                            "source_excerpt": "script",
                            "story_beat": "The scene begins.",
                            "script": "new shot",
                            "duration_ms": 3000,
                            "asset_ids": [],
                        }
                    ]
                }
            )
            record.response_data = {"finish_reason": "stop"}
            GenerationBusinessService(session).save_text_result(tid, record.id)
            finish(session.get(AsyncTask, tid), "succeeded")
        before = board.list(p, e)
        assert [row["script"] for row in before["items"]] == ["old shot"]
        payload = {
            "mode": mode,
            "confirm_replace": mode == "replace",
            "content_version": "3",
            "storyboard_version": before["storyboard_version"],
        }
        business = GenerationBusinessService(session)
        adopted = business.apply_storyboard(p, e, tid, payload)
        replay = business.apply_storyboard(p, e, tid, payload)
        assert replay["already_applied"] and replay["shot_ids"] == adopted["shot_ids"]
        generated = board.list(p, e)["items"]
        assert [row["script"] for row in generated] == (
            ["old shot", "new shot"] if mode == "append" else ["new shot"]
        )
        assert generated[-1]["duration_ms"] == 3000
        assert generated[-1]["source_excerpt"] == "script"
        with session.begin():
            archived = session.get(ShotScript, int(old["shot"]["id"])).deleted_at is not None
            assert archived == (mode == "replace")
            assert len(list(session.scalars(select(ScriptShotRecord)))) == 1


def test_worker_commits_raw_text_then_recovers_local_save_without_model_call():
    from generation_fixtures import config, generation_session, settings
    from sqlalchemy.orm import sessionmaker
    from test_episode_writing import setup

    from short_drama.ai.types import GenerationResult
    from short_drama.core.config import Settings
    from short_drama.domain import AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.generation_execution_service import GenerationExecutionService

    with generation_session() as session:
        config(session, "text")
        p, e, writing = setup(session)
        writing.save_novel(p, e, {"content_version": "1", "content": "novel"})
        task, _ = AIGenerationService(session, settings).create(
            "text",
            {
                "source": {
                    "scene": "novel_script",
                    "project_id": str(p),
                    "episode_id": str(e),
                    "content_version": "2",
                }
            },
            "worker-business",
        )
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        worker = GenerationExecutionService(factory, Settings(_env_file=None), object(), None)
        tid = int(task["generation_id"])
        claimed, record, token = worker.store.claim_execution(tid, 1)
        worker._store_result(
            claimed,
            record,
            1,
            token,
            GenerationResult(
                status="succeeded", adapter="openai_chat", text="Generated", finish_reason="stop"
            ),
        )
        with factory() as reading:
            queued = reading.get(AsyncTask, tid)
            assert queued.next_action == "save" and queued.status == "running"
            version = queued.message_version
        assert writing.candidates(p, e)["total"] == 0
        worker.execute(tid, version)
        worker.execute(tid, version)
        assert writing.candidates(p, e)["total"] == 1
        assert AIGenerationService(session, settings).detail(tid)["status"] == "succeeded"
