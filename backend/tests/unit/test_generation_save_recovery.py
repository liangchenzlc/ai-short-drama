from datetime import timedelta

import pytest
from generation_fixtures import config, generation_session
from generation_fixtures import settings as fixture_settings
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from short_drama.core.config import Settings
from short_drama.dao.task_runtime_dao import TaskRuntimeDAO
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.base import utcnow
from short_drama.tasks.celery_app import topology
from short_drama.tasks.recovery import recover


@pytest.fixture
def archived_task():
    with generation_session() as session:
        config(session)
        summary, _ = AIGenerationService(session, fixture_settings).create(
            "image", {"input": {"prompt": "scene"}}, "saved-result"
        )
        task_id = int(summary["generation_id"])
        task = session.get(AsyncTask, task_id)
        record = session.scalar(select(AIGenerationRecord))
        before = utcnow() - timedelta(minutes=5)
        task.created_at = record.created_at = before - timedelta(seconds=1)
        task.status = "running"
        task.started_at = before
        task.next_action = "save"
        task.message_status = "published"
        task.updated_at = task.next_run_at = before
        record.status = "succeeded"
        record.finished_at = before
        record.response_data = {
            "archive_started_at": before.isoformat(),
            "expected_count": 1,
            "media_manifest": [
                {"asset_id": "123", "output_index": 1, "locator": "minio://image/result.png"}
            ],
        }
        session.commit()
        yield sessionmaker(bind=session.get_bind(), expire_on_commit=False), task_id, record.id


def test_lost_save_is_republished_once_and_old_or_duplicate_delivery_cannot_claim(archived_task):
    factory, task_id, record_id = archived_task
    settings = Settings(_env_file=None)
    assert recover(factory, settings) == 1
    assert recover(factory, settings) == 0
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        assert (task.next_action, task.message_status, task.message_version) == (
            "save",
            "pending",
            2,
        )
        record = session.get(AIGenerationRecord, record_id)
        assert record.status == "succeeded"
        assert record.response_data["media_manifest"][0]["asset_id"] == "123"
    store = TaskRuntimeDAO(factory, settings)
    assert store.claim_execution(task_id, 1) is None
    claimed, record, _token = store.claim_execution(task_id, 2)
    assert claimed.next_action == "save" and record.status == "succeeded"
    assert store.claim_execution(task_id, 2) is None
    assert recover(factory, settings) == 0


@pytest.mark.parametrize("action", ["submit", "poll"])
def test_published_provider_actions_are_never_replayed(archived_task, action):
    factory, task_id, _ = archived_task
    with factory.begin() as session:
        session.get(AsyncTask, task_id).next_action = action
    assert recover(factory, Settings(_env_file=None)) == 0
    with factory() as session:
        assert session.get(AsyncTask, task_id).message_version == 1


@pytest.mark.parametrize("change", ["recent", "active", "no_media", "unknown", "terminal"])
def test_save_recovery_requires_stale_unclaimed_successful_result(archived_task, change):
    factory, task_id, record_id = archived_task
    with factory.begin() as session:
        task = session.get(AsyncTask, task_id)
        record = session.get(AIGenerationRecord, record_id)
        if change == "recent":
            task.updated_at = utcnow()
        elif change == "active":
            task.lock_token = "active-worker"
            task.locked_until = utcnow() + timedelta(minutes=1)
        elif change == "no_media":
            record.response_data = {"media_manifest": [{"save_error": {"code": "missing_output"}}]}
        elif change == "unknown":
            record.status = "unknown"
        else:
            task.status = "succeeded"
            task.finished_at = utcnow()
    assert recover(factory, Settings(_env_file=None)) == 0


def test_expired_archive_budget_stops_without_extending_budget_or_regenerating(archived_task):
    factory, task_id, record_id = archived_task
    with factory.begin() as session:
        record = session.get(AIGenerationRecord, record_id)
        record.response_data = {
            **record.response_data,
            "archive_started_at": (utcnow() - timedelta(days=2)).isoformat(),
        }
    assert recover(factory, Settings(_env_file=None)) == 1
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        assert task.status == "failed" and task.error["code"] == "archive_timeout"
        assert task.next_action is None
        assert session.get(AIGenerationRecord, record_id).status == "succeeded"


@pytest.mark.parametrize("namespace,prefix", [("short_drama", ""), ("studio_dev", "studio_dev.")])
def test_worker_and_publisher_topology_uses_same_configured_namespace(namespace, prefix):
    exchange, dead_exchange, queues = topology(
        Settings(_env_file=None, generation_queue_namespace=namespace)
    )
    for kind, queue in queues.items():
        assert queue.name == f"{prefix}tasks.ai.{kind}"
        assert queue.exchange.name == exchange.name == f"{namespace}.tasks"
        assert queue.queue_arguments["x-dead-letter-exchange"] == dead_exchange.name
