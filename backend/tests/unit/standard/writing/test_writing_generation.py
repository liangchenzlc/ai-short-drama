"""Business invariants; provider/queue integration is covered separately."""

import pytest
from sqlalchemy import func, select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIGenerationRecord, AsyncTask, ProjectScript
from short_drama.service.creation.writing.script.documents import ProjectWritingService
from short_drama.service.generations.admission.business import GenerationBusinessService
from short_drama.service.generations.admission.generation import AIGenerationService
from short_drama.service.generations.runtime.lifecycle import finish
from tests.support.generation_fixtures import config, generation_session, settings
from tests.unit.standard.writing.test_project_writing import setup


def admit(session, project, scene="script_rewrite", kind=None, **extra):
    writing = ProjectWritingService(session)
    state = writing.get(project)
    if state["editing_script"] is None:
        writing.save_script(
            project,
            {"script_id": None, "content": "原正文", "content_version": state["content_version"]},
        )
        state = writing.get(project)
    script = state["editing_script"]
    source = {
        "scene": "script_rewrite",
        "project_id": str(project),
        "content_version": state["content_version"],
        "script_id": script["id"],
        "start": 0,
        "end": len(script["content"]),
        "selected_text": script["content"],
        **extra,
    }
    body = {"source": source, "instructions": "保持人物性格"}
    return AIGenerationService(session, settings).create("text", body, "script-rewrite"), body


def output(session, task_id, content="生成的正文", reason="stop"):
    with session.begin():
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
        )
        record.text_content = content
        record.status = "succeeded"
        record.response_data = {"finish_reason": reason}
        return record.id


def apply(session, task_id, record_id):
    with session.begin():
        result = GenerationBusinessService(session).save_text_result(int(task_id), record_id)
        finish(session.get(AsyncTask, int(task_id)), "succeeded")
        return result


def test_rewrite_uses_code_points_and_changes_only_selected_occurrence_in_place():
    with generation_session() as session:
        config(session, "text")
        project, writing = setup(session)
        original = "😀开头\n同一句话\n转场\n同一句话\n结尾👨‍👩‍👧"
        saved = writing.save_script(
            project, {"script_id": None, "content": original, "content_version": "1"}
        )
        start = original.rindex("同一句话")
        (receipt, _), _ = admit(
            session,
            project,
            "script_rewrite",
            script_id=saved["script"]["id"],
            start=start,
            end=start + 4,
            selected_text="同一句话",
        )
        with session.begin():
            frozen = session.scalar(
                select(AIGenerationRecord).where(
                    AIGenerationRecord.task_id == int(receipt["generation_id"])
                )
            ).request_data
            assert "outline" not in frozen["source_snapshot"]
            assert frozen["source_snapshot"]["before"] == original[:start]
            assert frozen["source_snapshot"]["after"] == original[start + 4 :]
            assert "不可见旧大纲" not in str(frozen["input"])
            assert frozen["template_version"] == "script-rewrite-v2"
        record_id = output(session, receipt["generation_id"], "  只改这句\n")
        apply(session, receipt["generation_id"], record_id)
        current = writing.get(project)
        assert current["editing_script"]["id"] == saved["script"]["id"]
        assert (
            current["editing_script"]["content"]
            == original[:start] + "  只改这句\n" + original[start + 4 :]
        )
        assert current["content_version"] == "3"
        with session.begin():
            assert session.scalar(select(func.count()).select_from(ProjectScript)) == 1


@pytest.mark.parametrize("operation", ["save", "generate", "assets", "shots"])
def test_script_target_lock_covers_all_writers_and_downstream_sources(operation):
    with generation_session() as session:
        config(session, "text")
        project, writing = setup(session)
        saved = writing.save_script(
            project, {"script_id": None, "content": "旧正文", "content_version": "1"}
        )
        admit(session, project)
        script_id = saved["script"]["id"]
        with pytest.raises(WorkflowError) as caught:
            if operation == "save":
                writing.save_script(
                    project, {"script_id": script_id, "content": "新文本", "content_version": "2"}
                )
            elif operation == "generate":
                AIGenerationService(session, settings).create(
                    "text",
                    {
                        "source": {
                            "scene": "script_rewrite",
                            "project_id": str(project),
                            "content_version": "2",
                            "script_id": script_id,
                            "start": 0,
                            "end": 3,
                            "selected_text": "旧正文",
                        },
                        "instructions": "改写对白",
                    },
                    "other",
                )
            else:
                AIGenerationService(session, settings).create(
                    "text",
                    {
                        "source": {
                            "scene": f"script_{operation}",
                            "project_id": str(project),
                            "script_id": script_id,
                            "content_version": "2",
                        }
                    },
                    "downstream",
                )
        assert caught.value.code == "writing_target_busy"
        assert writing.get(project)["editing_script"]["content"] == "旧正文"


@pytest.mark.parametrize(
    "content,reason,code",
    [
        ("", "stop", "empty_result"),
        ("残缺", "length", "text_truncated"),
        ("汉" * 349526, "stop", "writing_result_too_large"),
    ],
    ids=["empty", "truncated", "oversize"],
)
def test_invalid_output_keeps_previous_document_and_raw_result(content, reason, code):
    with generation_session() as session:
        config(session, "text")
        project, writing = setup(session)
        saved = writing.save_script(
            project, {"script_id": None, "content": "保留我", "content_version": "1"}
        )
        (receipt, _), _ = admit(session, project)
        rid = output(session, receipt["generation_id"], content, reason)
        with pytest.raises(WorkflowError) as caught:
            apply(session, receipt["generation_id"], rid)
        assert caught.value.code == code
        assert writing.get(project)["editing_script"] == saved["script"]
        with session.begin():
            assert session.get(AIGenerationRecord, rid).text_content == content


def test_cancelled_task_and_late_output_cannot_overwrite():
    with generation_session() as session:
        config(session, "text")
        project, writing = setup(session)
        (receipt, _), _ = admit(session, project)
        api = AIGenerationService(session, settings)
        api.cancel(receipt["generation_id"])
        assert writing.get(project)["active_writing_tasks"] == []
        rid = output(session, receipt["generation_id"])
        with pytest.raises(WorkflowError):
            apply(session, receipt["generation_id"], rid)
        assert writing.get(project)["editing_script"]["content"] == "原正文"


def test_stale_fingerprint_prevents_save_and_resume_without_regeneration():
    with generation_session() as session:
        config(session, "text")
        project, writing = setup(session)
        saved = writing.save_script(
            project, {"script_id": None, "content": "原正文", "content_version": "1"}
        )
        (receipt, _), _ = admit(session, project)
        tid = int(receipt["generation_id"])
        rid = output(session, tid)
        with session.begin():
            # Model output is recoverable, but an intervening target change fences it.
            session.get(ProjectScript, int(saved["script"]["id"])).content = "外部新正文"
            finish(session.get(AsyncTask, tid), "failed", {"code": "business_save_failed"})
        with pytest.raises(WorkflowError) as caught:
            AIGenerationService(session, settings).resume(tid)
        assert caught.value.code == "writing_target_changed"
        assert writing.get(project)["editing_script"]["content"] == "外部新正文"
        with session.begin():
            assert session.get(AIGenerationRecord, rid).text_content == "生成的正文"


def test_failed_atomic_write_resumes_saved_output_without_another_model_call(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import sessionmaker

    from short_drama.ai.types import GenerationResult
    from short_drama.core.config import Settings
    from short_drama.service.generations.execution.execution import GenerationExecutionService

    with generation_session() as session:
        config(session, "text")
        project, writing = setup(session)
        writing.save_script(
            project, {"script_id": None, "content": "原正文", "content_version": "1"}
        )
        (receipt, _), _ = admit(session, project)
        tid = int(receipt["generation_id"])
        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        # The object gateway has no generate method; every subsequent action must be local.
        worker = GenerationExecutionService(factory, Settings(_env_file=None), object(), None)
        task, record, token = worker.store.claim_execution(tid, 1)
        worker._store_result(
            task,
            record,
            1,
            token,
            GenerationResult(
                status="succeeded", adapter="openai_chat", text="完整生成正文", finish_reason="stop"
            ),
        )
        changed = ProjectWritingService._changed

        def fail_before_commit(*_):
            raise SQLAlchemyError("local save failure")

        monkeypatch.setattr(ProjectWritingService, "_changed", fail_before_commit)
        with factory() as reading:
            version = reading.get(AsyncTask, tid).message_version
        worker.execute(tid, version)
        session.expire_all()
        assert writing.get(project)["editing_script"]["content"] == "原正文"
        assert writing.get(project)["content_version"] == "2"
        with session.begin():
            raw = session.get(AIGenerationRecord, record.id)
            assert raw.text_content == "完整生成正文"
            assert not (raw.response_data or {}).get("business_result")
            finish(session.get(AsyncTask, tid), "failed", {"code": "business_save_failed"})
        monkeypatch.setattr(ProjectWritingService, "_changed", changed)
        api = AIGenerationService(session, settings)
        assert api.detail(tid)["can_resume"]
        api.resume(tid)
        with factory() as reading:
            version = reading.get(AsyncTask, tid).message_version
        worker.execute(tid, version)
        worker.execute(tid, version)
        session.expire_all()
        assert api.detail(tid)["status"] == "succeeded"
        assert writing.get(project)["editing_script"]["content"] == "完整生成正文"
        assert writing.get(project)["content_version"] == "3"
        with session.begin():
            assert session.scalar(select(func.count()).select_from(AIGenerationRecord)) == 1
