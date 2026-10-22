"""Task lease queries; callers own transactions and lifecycle policy."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from short_drama.core.time import utcnow
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.tasks.state import ACTIONS, TERMINAL


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


class TaskRuntimeDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def publish_candidate(self, now: datetime) -> AsyncTask | None:
        return self.session.scalar(
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

    def task(self, task_id: int) -> AsyncTask | None:
        return self.session.scalar(
            select(AsyncTask).where(AsyncTask.id == task_id).with_for_update()
        )

    def renew_lease(
        self, task_id: int, version: int, token: str, now: datetime, locked_until: datetime
    ) -> bool:
        result = self.session.execute(
            update(AsyncTask)
            .where(
                AsyncTask.id == task_id,
                AsyncTask.message_version == version,
                AsyncTask.lock_token == token,
                AsyncTask.locked_until > now,
                AsyncTask.status.not_in(TERMINAL),
            )
            .values(locked_until=locked_until, updated_at=now)
        )
        return result.rowcount == 1
