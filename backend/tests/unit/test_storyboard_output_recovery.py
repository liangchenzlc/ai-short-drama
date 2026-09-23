import json

import pytest
from generation_fixtures import config, generation_session, settings
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_episode_writing import setup

from short_drama.ai.types import GenerationResult
from short_drama.core.config import Settings
from short_drama.dao.task_runtime_dao import finish
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.schemas.storyboard_result import parse_storyboard_result
from short_drama.service.ai_generation_service import AIGenerationService, resume_action
from short_drama.service.generation_execution_service import GenerationExecutionService

SOURCE = "九月，阴天。\n\n灰白色的宿舍楼被暮色笼罩，一扇扇窗户像排列整齐的眼睛。"
SHOT = {
    "title": "暮色中的三号楼",
    "source_excerpt": SOURCE,
    "story_beat": "建立阴郁校园环境，将宿舍楼塑造成具有窥视感的主要威胁空间。",
    "visual_script": (
        "远景固定构图，阴天暮色压住灰白色宿舍楼，整齐窗格占满立面，如一排沉默的眼睛。"
        "画面缓慢向楼体推进，不出现额外人物特写。"
    ),
    "duration_ms": 4000,
    "asset_ids": ["360615771405029376"],
}


def test_visual_script_is_normalized_without_changing_the_generated_text():
    content = json.dumps({"shots": [SHOT]}, ensure_ascii=False)
    result = parse_storyboard_result(content, {360615771405029376}, SOURCE)

    expected = {**SHOT, "script": SHOT["visual_script"]}
    del expected["visual_script"]
    assert result == {"shots": [expected]}
    assert json.loads(content) == {"shots": [SHOT]}


@pytest.mark.parametrize(
    "changes",
    [
        {"script": "conflicting script"},
        {"visual_script": ""},
        {"visual_script": "x" * 32769},
        {"source_excerpt": "原文不存在的片段"},
        {"asset_ids": ["999"]},
        {"duration_ms": 10001},
        {"unexpected": "extra"},
    ],
)
def test_visual_script_alias_preserves_strict_validation(changes):
    with pytest.raises(ValueError):
        parse_storyboard_result(
            json.dumps({"shots": [{**SHOT, **changes}]}), {360615771405029376}, SOURCE
        )


@pytest.mark.parametrize("invalid_content", ['{"shots":[]}', "not json"])
def test_still_invalid_storyboard_is_not_offered_as_recoverable(invalid_content):
    from types import SimpleNamespace

    task = SimpleNamespace(
        status="failed", locked_until=None, error={"code": "invalid_structured_output"}
    )
    record = SimpleNamespace(
        status="succeeded",
        text_content=invalid_content,
        response_data={"finish_reason": "stop"},
        request_data={
            "source": {"scene": "script_shots"},
            "source_snapshot": {"content": SOURCE, "assets": []},
        },
    )
    assert resume_action(task, record) is None


def test_failed_storyboard_recovers_saved_text_without_another_model_call():
    with generation_session() as session:
        config(session, "text")
        project_id, episode_id, writing = setup(session)
        saved = writing.save_script(
            project_id,
            episode_id,
            {"content_version": "1", "script_id": None, "content": SOURCE},
        )
        script_id = saved["script"]["id"]
        writing.confirm(project_id, episode_id, script_id, {"content_version": "2"})
        service = AIGenerationService(session, settings)
        created, _ = service.create(
            "text",
            {
                "source": {
                    "scene": "script_shots",
                    "project_id": str(project_id),
                    "episode_id": str(episode_id),
                    "script_id": script_id,
                    "content_version": "3",
                },
                "storyboard": {"average_shot_duration_ms": 5000},
            },
            "recover-visual-script",
        )
        task_id = int(created["generation_id"])
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        worker = GenerationExecutionService(factory, Settings(_env_file=None), object(), None)
        task, record, token = worker.store.claim_execution(task_id, 1)
        raw_text = json.dumps({"shots": [{**SHOT, "asset_ids": []}]}, ensure_ascii=False)
        worker._store_result(
            task,
            record,
            1,
            token,
            GenerationResult(
                status="succeeded", adapter="openai_chat", text=raw_text, finish_reason="stop"
            ),
        )
        with factory.begin() as transaction:
            finish(
                transaction.get(AsyncTask, task_id),
                "failed",
                {"code": "invalid_structured_output"},
            )
        session.expire_all()
        assert service.list()["items"][0]["can_resume"] is True
        failed = service.detail(task_id)
        assert failed["can_resume"] is True
        assert "无需重新生成" in failed["error"]["message"]
        assert service.resume(task_id)["next_action"] == "save"
        with factory() as reading:
            version = reading.get(AsyncTask, task_id).message_version
        worker.execute(task_id, version)
        worker.execute(task_id, version)
        session.expire_all()
        detail = service.detail(task_id)
        assert detail["status"] == "succeeded"
        assert detail["result"]["text"]["content"] == raw_text
        assert detail["result"]["business"]["shots"][0]["script"] == SHOT["visual_script"]
        assert "visual_script" not in detail["result"]["business"]["shots"][0]
        assert session.scalar(select(func.count()).select_from(AIGenerationRecord)) == 1
