"""Task transaction ownership, paid-submit authorization and image-group coordination."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.orm import object_session

from short_drama.core.time import utcnow
from short_drama.dao.generations.runtime.lifecycle import TaskRuntimeDAO, latest_record
from short_drama.domain import AsyncTask
from short_drama.service.generations.runtime.access import (
    lock_resource_project,
    may_submit,
    submission_error,
)
from short_drama.service.media.image_groups.groups import ImageGroupService
from short_drama.tasks.runtime.state import ACTIONS, TERMINAL, recovery_action


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
    _image_task_state(task)


def _image_task_state(task):
    session = object_session(task) if inspect(task, raiseerr=False) is not None else None
    if session is not None and task.service_type in {"image", "text"}:
        ImageGroupService(session).task_state(task)


def finish(task, status, error=None):
    task.status = status
    task.error = error
    task.finished_at = utcnow() if status in TERMINAL else None
    task.next_action = task.next_run_at = None
    task.message_status = "idle"
    task.message_version += 1
    task.lock_token = task.locked_until = None
    task.updated_at = utcnow()
    _image_task_state(task)


def publication_failed(task):
    task.lock_token = task.locked_until = None
    task.updated_at = utcnow()
    if task.publish_count >= 3:
        task.status = "failed"
        task.finished_at = task.updated_at
        task.error = {"code": "message_delivery_unknown", "message": "消息投递待核对，可恢复投递"}
        task.message_status = "idle"
        task.next_run_at = None
        _image_task_state(task)
    else:
        task.message_status = "pending"
        task.next_run_at = utcnow() + timedelta(seconds=2**task.publish_count)


class TaskRuntimeService:
    def __init__(self, factory, settings):
        self.factory = factory
        self.settings = settings

    def claim_publish(self):
        now = utcnow()
        with self.factory.begin() as session:
            task = TaskRuntimeDAO(session).publish_candidate(now)
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
            task = TaskRuntimeDAO(session).task(task_id)
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
            lock_resource_project(session, AsyncTask, task_id)
            task = TaskRuntimeDAO(session).task(task_id)
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
            if (
                record.status == "prepared"
                and task.next_action == "submit"
                and not may_submit(session, task, settings=self.settings)
            ):
                finish(task, "cancelled", submission_error(session, task))
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
            return TaskRuntimeDAO(session).renew_lease(
                task_id,
                version,
                token,
                now,
                now + timedelta(seconds=self.settings.generation_lease_seconds),
            )
