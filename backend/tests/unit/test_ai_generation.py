import pytest
from generation_fixtures import config, generation_session, settings


def test_upstream_failure_explains_unknown_result_and_only_exposes_valid_http_status():
    from short_drama.service.ai_generation_service import safe_error

    result = safe_error({"code": "upstream_unavailable", "http_status": 504, "message": "secret"})
    assert "模型服务或中转站" in result["message"]
    assert "504" in result["message"]
    assert "未自动重发" in result["message"]
    assert result["http_status"] == 504
    assert "secret" not in str(result)
    for invalid in ("secret", True, 999, 99):
        assert "http_status" not in safe_error(
            {"code": "upstream_unavailable", "http_status": invalid}
        )


@pytest.mark.parametrize(
    "parameters", [{"resolution": "2K"}, {"aspect": "16:9"}, {"aspect": "4:3", "count": 1}]
)
def test_image_size_rejection_is_actionable_and_creates_no_task(parameters):
    from sqlalchemy import select

    from short_drama.core.exceptions import BusinessError
    from short_drama.domain import AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        model = config(session)
        model.base_url = "https://relay.example"
        model.model_key = "gpt-image-2"
        session.commit()
        with pytest.raises(BusinessError) as error:
            AIGenerationService(session, settings).create(
                "image", {"input": {"prompt": "rain"}, "parameters": parameters}, "bad-size"
            )
        assert error.value.code == "generation_unsupported_image_size"
        assert session.scalar(select(AsyncTask)) is None


def test_missing_default_configuration_has_distinct_error():
    from short_drama.core.exceptions import BusinessError
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        model = config(session)
        model.is_default = 0
        session.commit()
        model_id = str(model.id)
        service = AIGenerationService(session, settings)
        with pytest.raises(BusinessError) as error:
            service.create("image", {"input": {"prompt": "rain"}}, "missing-default")
        assert error.value.code == "generation_default_config_missing"
        result, created = service.create(
            "image", {"config_id": model_id, "input": {"prompt": "rain"}}, "explicit"
        )
        assert created and result["status"] == "queued"


def test_generation_domain_matches_fixed_storage_contract():
    from short_drama import domain

    assert hasattr(domain, "AsyncTask"), "Generation task mapping is missing"
    assert len(domain.AsyncTask.__table__.columns) == 19
    assert len(domain.AIGenerationRecord.__table__.columns) == 17
    assert len(domain.MediaAsset.__table__.columns) == 9
    assert "capability_cache" in domain.AIModelConfig.__table__.columns


def test_generation_input_rejects_unknown_scene_and_excess_outputs():
    from pydantic import ValidationError

    from short_drama.schemas.ai_generation import ImageGenerationCreate, TextGenerationCreate

    with pytest.raises(ValidationError):
        ImageGenerationCreate.model_validate(
            {"input": {"prompt": "hello"}, "parameters": {"count": 5}}
        )
    with pytest.raises(ValidationError):
        TextGenerationCreate.model_validate(
            {
                "input": {"messages": [{"role": "user", "content": "hi"}]},
                "source": {"scene": "shot_image", "shot_id": "1", "layout": "single"},
            }
        )


def test_idempotency_freezes_config_and_conflicts_on_changed_input():
    from sqlalchemy import select

    from short_drama.core.exceptions import BusinessError, Conflict
    from short_drama.domain import AIGenerationRecord
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        model = config(session)
        service = AIGenerationService(session, settings)
        payload = {"input": {"prompt": "rain"}}
        with pytest.raises(BusinessError):
            service.create("image", payload, "")
        first, created = service.create("image", payload, "original")
        assert created is True
        model.enabled = 0
        model.is_default = 0
        session.commit()
        replay, created = service.create("image", payload, "original")
        assert replay["generation_id"] == first["generation_id"]
        assert created is False
        with pytest.raises(Conflict):
            service.create("image", {"input": {"prompt": "sun"}}, "original")
        record = session.scalar(select(AIGenerationRecord))
        assert record.credential_cipher == "encrypted-secret"
        assert record.status == "prepared" and record.call_no == 1
        assert record.config_snapshot["archive_budget_seconds"] == 86400


def test_cancel_prepared_invalidates_message_but_running_only_records_intent():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord, AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        config(session)
        service = AIGenerationService(session, settings)
        first, _ = service.create("image", {"input": {"prompt": "rain"}}, "cancel")
        assert service.cancel(first["generation_id"])["status"] == "cancelled"
        second, _ = service.create("image", {"input": {"prompt": "rain"}}, "running")
        task = session.get(AsyncTask, int(second["generation_id"]))
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        task.status = "running"
        record.status = "sent"
        session.commit()
        assert service.cancel(second["generation_id"])["status"] == "running"
        assert task.cancel_requested == 1


def test_resume_never_resubmits_unknown_acceptance_and_advances_once():
    from sqlalchemy import select

    from short_drama.core.exceptions import Conflict
    from short_drama.domain import AIGenerationRecord, AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        config(session)
        service = AIGenerationService(session, settings)
        summary, _ = service.create("image", {"input": {"prompt": "rain"}}, "resume")
        task_id = int(summary["generation_id"])
        task = session.get(AsyncTask, int(summary["generation_id"]))
        record = session.scalar(select(AIGenerationRecord))
        task.status = "failed"
        task.finished_at = task.updated_at
        task.message_status = "idle"
        task.error = {"code": "message_delivery_unknown"}
        record.status = "sent"
        session.commit()
        with pytest.raises(Conflict):
            service.resume(task_id)
        record.status = "prepared"
        session.commit()
        assert service.resume(task_id)["status"] == "queued"
        assert task.message_version == 2 and task.publish_count == 0
        service.resume(task_id)
        assert task.message_version == 2


def test_retry_creates_new_history_and_unknown_cannot_retry():
    from short_drama.core.exceptions import Conflict
    from short_drama.domain import AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        config(session)
        service = AIGenerationService(session, settings)
        first, _ = service.create("image", {"input": {"prompt": "rain"}}, "old")
        service.cancel(first["generation_id"])
        retry, created = service.retry(first["generation_id"], {}, "new")
        assert created and retry["generation_id"] != first["generation_id"]
        assert retry["retry_of_id"] == first["generation_id"]
        assert service.retry(first["generation_id"], {}, "new")[1] is False
        task = session.get(AsyncTask, int(retry["generation_id"]))
        task.status = "failed"
        task.finished_at = task.updated_at
        task.error = {"code": "upstream_unavailable"}
        from sqlalchemy import select

        from short_drama.domain import AIGenerationRecord

        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        record.status = "unknown"
        session.commit()
        assert service.detail(task.id)["can_retry"] is False
        with pytest.raises(Conflict):
            service.retry(retry["generation_id"], {}, "unsafe")


def test_safe_history_keeps_text_and_usage_but_never_raw_response_or_credentials():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        config(session, "text")
        service = AIGenerationService(session, settings)
        created, _ = service.create(
            "text", {"input": {"messages": [{"role": "user", "content": "hi"}]}}, "text"
        )
        record = session.scalar(select(AIGenerationRecord))
        record.text_content = "A scene"
        record.response_data = {
            "usage": None,
            "raw": "secret-value",
            "download_credential": "secret-value",
        }
        record.error = {"code": "provider_auth", "message": "secret-value"}
        session.commit()
        assert service.detail(created["generation_id"])["result"]["text"]["content"] == "A scene"
        history = service.records(created["generation_id"])
        assert "secret-value" not in str(history)
        assert "encrypted-secret" not in str(history)
        assert history["items"][0]["usage"] == {}


def test_archive_resume_preserves_cancel_intent_and_never_resubmits_generation():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord, AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.base import utcnow

    with generation_session() as session:
        config(session)
        service = AIGenerationService(session, settings)
        created, _ = service.create("image", {"input": {"prompt": "rain"}}, "archive")
        identifier = int(created["generation_id"])
        task = session.get(AsyncTask, identifier)
        record = session.scalar(select(AIGenerationRecord))
        record.status = "succeeded"
        record.response_data = {
            "media_manifest": [{"asset_id": "123", "source_cipher": "encrypted-download-source"}]
        }
        task.status, task.finished_at = "failed", utcnow()
        task.cancel_requested = 1
        session.commit()
        assert service.resume(identifier)["next_action"] == "save"
        assert task.cancel_requested == 1
        assert task.message_version == 2


def test_unknown_video_protocol_rejected_before_task_creation():
    from sqlalchemy import func, select

    from short_drama.core.exceptions import BusinessError
    from short_drama.domain import AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService

    with generation_session() as session:
        model = config(session, "video")
        model.base_url = "https://private.example/v1"
        session.commit()
        with pytest.raises(BusinessError, match="not supported"):
            AIGenerationService(session, settings).create(
                "video", {"input": {"prompt": "rain"}}, "video"
            )
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_history_recovery_uses_latest_call_evidence():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord, AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.base import utcnow

    with generation_session() as session:
        config(session)
        service = AIGenerationService(session, settings)
        result, _ = service.create("image", {"input": {"prompt": "rain"}}, "two-calls")
        task = session.get(AsyncTask, int(result["generation_id"]))
        record = session.scalar(select(AIGenerationRecord))
        now = utcnow()
        record.status = "failed"
        task.status, task.finished_at = "failed", now
        session.add(
            AIGenerationRecord(
                id=77,
                task_id=task.id,
                call_no=2,
                config_id=record.config_id,
                config_snapshot=record.config_snapshot,
                request_data=record.request_data,
                status="succeeded",
                response_data={
                    "media_manifest": [
                        {"asset_id": "1", "source_cipher": "encrypted-download-source"}
                    ]
                },
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        assert service.list()["items"][0]["can_resume"] is True


def test_irrecoverable_inline_result_is_not_offered_as_resumable():
    from sqlalchemy import select

    from short_drama.core.exceptions import Conflict
    from short_drama.domain import AIGenerationRecord, AsyncTask
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.base import utcnow

    with generation_session() as session:
        config(session)
        service = AIGenerationService(session, settings)
        created, _ = service.create("image", {"input": {"prompt": "rain"}}, "lost-inline")
        identifier = int(created["generation_id"])
        task = session.get(AsyncTask, identifier)
        record = session.scalar(select(AIGenerationRecord))
        record.status = "succeeded"
        record.response_data = {
            "media_manifest": [{"asset_id": "1", "save_error": {"code": "result_unavailable"}}]
        }
        task.status, task.finished_at = "failed", utcnow()
        session.commit()
        assert service.detail(identifier)["can_resume"] is False
        with pytest.raises(Conflict):
            service.resume(identifier)
