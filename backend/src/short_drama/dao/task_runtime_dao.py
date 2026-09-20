"""Short transactions implementing task ownership; no external I/O under locks."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update

from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.base import utcnow
from short_drama.tasks.state import ACTIONS, TERMINAL, recovery_action


class LeaseLost(Exception):
    pass


def latest_record(session, task_id):
    return session.scalar(
        select(AIGenerationRecord)
        .where(AIGenerationRecord.task_id == task_id)
        .order_by(AIGenerationRecord.call_no.desc())
        .limit(1)
    )


def owned_task(session, task_id, version, token):
    task = session.scalar(select(AsyncTask).where(AsyncTask.id == task_id).with_for_update())
    if (
        task is None
        or task.status in TERMINAL
        or task.message_version != version
        or task.lock_token != token
        or task.locked_until is None
        or task.locked_until <= utcnow()
    ):
        raise LeaseLost()
    return task


def schedule(task, action, delay=0, *, status="running"):
    now = utcnow()
    task.status = status
    task.finished_at = None
    task.next_action = action
    task.next_run_at = now + timedelta(seconds=delay)
    task.message_version += 1
    task.message_status = "pending"
    task.publish_count = 0
    task.lock_token = task.locked_until = None
    task.updated_at = now


def finish(task, status, error=None):
    task.status = status
    task.error = error
    task.finished_at = utcnow() if status in TERMINAL else None
    task.next_action = task.next_run_at = None
    task.message_status = "idle"
    task.message_version += 1
    task.lock_token = task.locked_until = None
    task.updated_at = utcnow()


def publication_failed(task):
    task.lock_token = task.locked_until = None
    task.updated_at = utcnow()
    if task.publish_count >= 3:
        task.status = "failed"
        task.finished_at = task.updated_at
        task.error = {"code": "message_delivery_unknown", "message": "消息投递待核对，可恢复投递"}
        task.message_status = "idle"
        task.next_run_at = None
    else:
        task.message_status = "pending"
        task.next_run_at = utcnow() + timedelta(seconds=2**task.publish_count)


class TaskRuntimeDAO:
    def __init__(self, factory, settings):
        self.factory = factory
        self.settings = settings

    def claim_publish(self):
        now = utcnow()
        with self.factory.begin() as session:
            task = session.scalar(
                select(AsyncTask)
                .where(
                    AsyncTask.message_status == "pending",
                    AsyncTask.next_run_at <= now,
                    AsyncTask.status.not_in(TERMINAL),
                    AsyncTask.next_action.in_(ACTIONS),
                )
                .order_by(AsyncTask.next_run_at, AsyncTask.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return None
            task.message_status = "publishing"
            task.lock_token = uuid4().hex
            task.locked_until = now + timedelta(
                seconds=self.settings.generation_publish_lease_seconds
            )
            task.publish_count += 1
            task.updated_at = now
            return {
                "task_id": task.id,
                "version": task.message_version,
                "token": task.lock_token,
                "kind": task.service_type,
            }

    def publish_result(self, task_id, version, token, kind=None, *, success):
        with self.factory.begin() as session:
            task = session.scalar(
                select(AsyncTask).where(AsyncTask.id == task_id).with_for_update()
            )
            if (
                task is None
                or task.message_version != version
                or task.lock_token != token
                or task.message_status != "publishing"
            ):
                return False
            if success:
                task.message_status = "published"
                task.lock_token = task.locked_until = None
                task.updated_at = utcnow()
            else:
                publication_failed(task)
            return True

    def claim_execution(self, task_id, version):
        now = utcnow()
        with self.factory.begin() as session:
            task = session.scalar(
                select(AsyncTask).where(AsyncTask.id == task_id).with_for_update()
            )
            if task is None or task.message_version != version or task.status in TERMINAL:
                return None
            record = latest_record(session, task_id)
            if record is None or task.next_action not in ACTIONS:
                return None
            if task.message_status == "idle":
                if (
                    (task.error or {}).get("code") != "message_delivery_unknown"
                    or task.lock_token is not None
                    or recovery_action(record) != task.next_action
                ):
                    return None
            elif task.message_status not in {"pending", "publishing", "published"}:
                return None
            if task.cancel_requested and record.status == "prepared":
                finish(task, "cancelled")
                return None
            task.message_status = "idle"
            task.lock_token = uuid4().hex
            task.locked_until = now + timedelta(seconds=self.settings.generation_lease_seconds)
            if (task.error or {}).get("code") == "message_delivery_unknown":
                task.error = None
            task.status = "running"
            task.started_at = task.started_at or now
            task.updated_at = now
            session.flush()
            return task, record, task.lock_token

    def heartbeat(self, task_id, version, token):
        now = utcnow()
        with self.factory.begin() as session:
            result = session.execute(
                update(AsyncTask)
                .where(
                    AsyncTask.id == task_id,
                    AsyncTask.message_version == version,
                    AsyncTask.lock_token == token,
                    AsyncTask.locked_until > now,
                    AsyncTask.status.not_in(TERMINAL),
                )
                .values(
                    locked_until=now + timedelta(seconds=self.settings.generation_lease_seconds),
                    updated_at=now,
                )
            )
            return result.rowcount == 1
