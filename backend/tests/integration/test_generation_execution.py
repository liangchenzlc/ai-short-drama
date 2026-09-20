"""Concurrency/recovery invariants against an isolated real MySQL schema."""

from datetime import timedelta
from uuid import uuid4

import pytest

from short_drama.core.config import Settings
from short_drama.db.session import session_factory
from short_drama.domain import AIGenerationRecord, AIModelConfig, AsyncTask
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


def seeded(factory, kind="text"):
    now = utcnow()
    with factory.begin() as session:
        config = AIModelConfig(
            id=next_id(),
            service_type=kind,
            name="runtime test",
            model_key="fixture",
            provider="fixture",
            base_url="https://example.com",
        )
        session.add(config)
        session.flush()
        task = AsyncTask(
            id=next_id(),
            service_type=kind,
            status="queued",
            idempotency_key=str(uuid4()),
            request_hash="a" * 64,
            next_action="submit",
            next_run_at=now,
            message_status="pending",
            message_version=1,
            publish_count=0,
            cancel_requested=0,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.flush()
        record = AIGenerationRecord(
            id=next_id(),
            task_id=task.id,
            call_no=1,
            config_id=config.id,
            status="prepared",
            config_snapshot={
                "service_type": kind,
                "base_url": "https://example.com/v1",
                "model_key": "fixture",
                "budget_seconds": 180,
                "archive_budget_seconds": 86400,
            },
            request_data={"input": {"messages": []}, "parameters": {}},
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        return task.id, record.id


def test_published_is_not_republished_and_early_consumer_wins(mysql_engine, db_session):
    from short_drama.dao.task_runtime_dao import TaskRuntimeDAO

    factory = session_factory(mysql_engine)
    store = TaskRuntimeDAO(factory, Settings(_env_file=None))
    task_id, _ = seeded(factory)
    publication = store.claim_publish()
    assert publication["task_id"] == task_id
    claim = store.claim_execution(task_id, 1)
    assert claim is not None
    assert not store.publish_result(**publication, success=True)
    assert store.claim_publish() is None
    assert store.claim_execution(task_id, 1) is None
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        assert task.message_status == "idle" and task.lock_token == claim[2]


def test_confirmed_queue_backlog_never_becomes_pending(mysql_engine, db_session):
    from short_drama.dao.task_runtime_dao import TaskRuntimeDAO
    from short_drama.tasks.recovery import recover

    factory = session_factory(mysql_engine)
    settings = Settings(_env_file=None)
    store = TaskRuntimeDAO(factory, settings)
    task_id, _ = seeded(factory)
    publication = store.claim_publish()
    assert store.publish_result(**publication, success=True)
    for _ in range(4):
        recover(factory, settings)
        assert store.claim_publish() is None
    with factory() as session:
        assert session.get(AsyncTask, task_id).message_status == "published"


def test_expired_sent_execution_becomes_unknown_without_post(mysql_engine, db_session):
    from short_drama.dao.task_runtime_dao import TaskRuntimeDAO
    from short_drama.tasks.recovery import recover

    factory = session_factory(mysql_engine)
    settings = Settings(_env_file=None)
    store = TaskRuntimeDAO(factory, settings)
    task_id, record_id = seeded(factory)
    store.claim_execution(task_id, 1)
    with factory.begin() as session:
        session.get(AsyncTask, task_id).locked_until = utcnow() - timedelta(seconds=1)
        record = session.get(AIGenerationRecord, record_id)
        record.status = "sent"
        record.started_at = utcnow()
    assert recover(factory, settings) == 1
    assert recover(factory, settings) == 0
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        assert task.status == "failed" and task.message_status == "idle"
        assert task.finished_at is not None
        assert task.lock_token is None and task.next_run_at is None


def test_expired_prepared_claim_creates_exactly_one_recovery_version(mysql_engine, db_session):
    from short_drama.dao.task_runtime_dao import TaskRuntimeDAO
    from short_drama.tasks.recovery import recover

    factory = session_factory(mysql_engine)
    settings = Settings(_env_file=None)
    store = TaskRuntimeDAO(factory, settings)
    task_id, _ = seeded(factory)
    store.claim_execution(task_id, 1)
    with factory.begin() as session:
        session.get(AsyncTask, task_id).locked_until = utcnow() - timedelta(seconds=1)
    assert recover(factory, settings) == 1
    assert recover(factory, settings) == 0
    assert store.claim_execution(task_id, 1) is None
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        assert task.message_version == 2 and task.next_action == "submit"


def test_worker_text_is_saved_once_and_duplicate_never_calls_again(mysql_engine, db_session):
    from short_drama.ai import GenerationResult
    from short_drama.service.generation_execution_service import GenerationExecutionService

    class Provider:
        calls = 0

        def validate(self, *args):
            return {"max_tokens": 32}

        def submit(self, *args, **kwargs):
            self.calls += 1
            return GenerationResult(
                status="succeeded",
                adapter="openai_chat.v1",
                text="生成的剧本文本",
                finish_reason="stop",
            )

    factory = session_factory(mysql_engine)
    task_id, record_id = seeded(factory)
    provider = Provider()
    executor = GenerationExecutionService(factory, Settings(_env_file=None), provider, None)
    executor.execute(task_id, 1)
    executor.execute(task_id, 1)
    with factory() as session:
        assert session.get(AsyncTask, task_id).status == "succeeded"
        record = session.get(AIGenerationRecord, record_id)
        assert record.text_content == "生成的剧本文本"
        assert record.request_data["resolved_parameters"] == {"max_tokens": 32}
    assert provider.calls == 1


@pytest.mark.parametrize(
    "error_code,http_status", [("timeout", None), ("upstream_unavailable", 504)]
)
def test_submit_timeout_is_unknown_without_automatic_post_retry(
    mysql_engine, db_session, error_code, http_status
):
    from short_drama.ai import GenerationError
    from short_drama.service.generation_execution_service import GenerationExecutionService
    from short_drama.tasks.recovery import recover

    class Provider:
        calls = 0

        def validate(self, *args):
            return {}

        def submit(self, *args, **kwargs):
            self.calls += 1
            raise GenerationError(error_code, accepted_unknown=True, http_status=http_status)

    factory = session_factory(mysql_engine)
    settings = Settings(_env_file=None)
    task_id, record_id = seeded(factory)
    provider = Provider()
    executor = GenerationExecutionService(factory, settings, provider, None)
    executor.execute(task_id, 1)
    recover(factory, settings)
    executor.execute(task_id, 1)
    with factory() as session:
        assert session.get(AsyncTask, task_id).status == "failed"
        assert session.get(AsyncTask, task_id).finished_at is not None
        assert session.get(AIGenerationRecord, record_id).status == "unknown"
        assert session.get(AsyncTask, task_id).error.get("http_status") == http_status
        assert session.get(AIGenerationRecord, record_id).error.get("http_status") == http_status
    assert provider.calls == 1


def test_late_video_result_receives_fresh_archive_window(mysql_engine, db_session):
    from short_drama.ai import GenerationResult
    from short_drama.service.generation_execution_service import GenerationExecutionService

    class Provider:
        def poll(self, *args):
            return GenerationResult(
                status="succeeded",
                adapter="ark_video.v1",
                outputs=[{"url": "https://example.com/result.mp4"}],
            )

    factory = session_factory(mysql_engine)
    task_id, record_id = seeded(factory, "video")
    with factory.begin() as session:
        task = session.get(AsyncTask, task_id)
        task.next_action = "poll"
        task.status = "running"
        # Move created_at as well to retain the table's audit-time ordering.
        task.created_at = task.started_at = utcnow() - timedelta(hours=2)
        record = session.get(AIGenerationRecord, record_id)
        record.status = "sent"
        record.provider_task_id = "fixture-video"
        record.adapter = "ark_video.v1"
    settings = Settings(
        _env_file=None, encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )
    executor = GenerationExecutionService(factory, settings, Provider(), None)
    executor.execute(task_id, 1)
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        record = session.get(AIGenerationRecord, record_id)
        assert task.status == "running" and task.next_action == "save"
        assert record.status == "succeeded"
        assert record.response_data["archive_started_at"] > task.started_at.isoformat()
        assert "https://example.com" not in str(record.response_data)


def test_expired_archive_action_does_not_download(mysql_engine, db_session):
    from short_drama.core.crypto import KeyCipher
    from short_drama.service.generation_execution_service import GenerationExecutionService

    class Provider:
        downloads = 0

        def download_media(self, *_args):
            self.downloads += 1
            raise RuntimeError("Download should not have started")

    factory = session_factory(mysql_engine)
    task_id, record_id = seeded(factory, "image")
    settings = Settings(
        _env_file=None, encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )
    with factory.begin() as session:
        task = session.get(AsyncTask, task_id)
        task.next_action = "save"
        record = session.get(AIGenerationRecord, record_id)
        record.status = "succeeded"
        record.response_data = {
            "archive_started_at": (utcnow() - timedelta(days=2)).isoformat(),
            "media_manifest": [
                {
                    "asset_id": str(next_id()),
                    "output_index": 1,
                    "media_type": "image",
                    "source_cipher": KeyCipher(settings.encryption_key.get_secret_value()).encrypt(
                        "https://example.com/a.png"
                    ),
                }
            ],
        }
    provider = Provider()
    executor = GenerationExecutionService(factory, settings, provider, None)
    executor.execute(task_id, 1)
    assert provider.downloads == 0
    with factory() as session:
        assert session.get(AsyncTask, task_id).status == "failed"


def test_inline_manifest_is_committed_before_object_upload(mysql_engine, db_session):
    import base64
    from io import BytesIO

    from PIL import Image

    from short_drama.ai import GenerationResult
    from short_drama.core.exceptions import NotFound
    from short_drama.service.generation_execution_service import GenerationExecutionService

    factory = session_factory(mysql_engine)
    task_id, record_id = seeded(factory, "image")
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, "PNG")
    observations = []

    class Provider:
        def validate(self, *args):
            return {}

        def submit(self, *args, **kwargs):
            return GenerationResult(
                status="succeeded",
                adapter="openai_images.v1",
                outputs=[{"base64": base64.b64encode(output.getvalue()).decode()}],
            )

    class Storage:
        def stat(self, *args):
            raise NotFound()

        def put(self, *args):
            with factory() as session:
                record = session.get(AIGenerationRecord, record_id)
                observations.append(bool((record.response_data or {}).get("media_manifest")))

    executor = GenerationExecutionService(factory, Settings(_env_file=None), Provider(), Storage())
    executor.execute(task_id, 1)
    assert observations == [True]


def test_only_finished_old_tasks_lose_retained_credentials(mysql_engine, db_session):
    from short_drama.tasks.recovery import purge_credentials

    factory = session_factory(mysql_engine)
    old_task, old_record = seeded(factory)
    active_task, active_record = seeded(factory)
    resumable_task, resumable_record = seeded(factory)
    old = utcnow() - timedelta(days=8)
    with factory.begin() as session:
        task = session.get(AsyncTask, old_task)
        task.created_at = task.finished_at = old
        task.status = "failed"
        session.get(AIGenerationRecord, old_record).credential_cipher = "old-ciphertext"
        session.get(AIGenerationRecord, active_record).credential_cipher = "active-ciphertext"
        paused = session.get(AsyncTask, resumable_task)
        paused.created_at = paused.finished_at = old
        paused.status = "failed"
        paused.error = {"code": "message_delivery_unknown"}
        session.get(AIGenerationRecord, resumable_record).credential_cipher = "resume-ciphertext"
    assert purge_credentials(factory) == 1
    with factory() as session:
        assert session.get(AIGenerationRecord, old_record).credential_cipher is None
        assert (
            session.get(AIGenerationRecord, active_record).credential_cipher == "active-ciphertext"
        )
        assert session.get(AsyncTask, active_task).status == "queued"
        assert (
            session.get(AIGenerationRecord, resumable_record).credential_cipher
            == "resume-ciphertext"
        )
