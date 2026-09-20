"""Recover only evidenced interrupted actions, never old published messages."""

from datetime import timedelta

from sqlalchemy import func, select, update

from short_drama.dao.task_runtime_dao import (
    finish,
    latest_record,
    publication_failed,
    schedule,
)
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.base import utcnow
from short_drama.tasks.state import TERMINAL, recovery_action


def recover(factory, settings, limit=100):
    recovered = 0
    with factory.begin() as session:
        tasks = session.scalars(
            select(AsyncTask)
            .where(
                AsyncTask.message_status.in_(("publishing", "idle")),
                AsyncTask.locked_until <= utcnow(),
                AsyncTask.status.not_in(TERMINAL),
            )
            .order_by(AsyncTask.locked_until)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for task in tasks:
            if task.message_status == "publishing":
                publication_failed(task)
            else:
                record = latest_record(session, task.id)
                action = recovery_action(record) if record else None
                if action:
                    if task.cancel_requested and action == "submit":
                        finish(task, "cancelled")
                    else:
                        schedule(task, action)
                else:
                    finish(
                        task,
                        "failed",
                        {
                            "code": "provider_acceptance_unknown",
                            "message": "调用中断，受理结果待核对",
                        },
                    )
                    if record and record.status == "sent":
                        record.status = "unknown"
                        record.updated_at = utcnow()
            recovered += 1
    return recovered


def purge_credentials(factory):
    """Keep history/media forever; drop only credentials of known-finished old tasks."""
    now = utcnow()
    with factory.begin() as session:
        result = session.execute(
            update(AIGenerationRecord)
            .where(
                AIGenerationRecord.credential_cipher.is_not(None),
                AIGenerationRecord.task_id.in_(
                    select(AsyncTask.id).where(
                        AsyncTask.status.in_(TERMINAL),
                        AsyncTask.finished_at <= now - timedelta(days=7),
                        func.coalesce(AsyncTask.error["code"].as_string(), "")
                        != "message_delivery_unknown",
                    )
                ),
            )
            .values(credential_cipher=None, updated_at=now)
        )
        return result.rowcount
