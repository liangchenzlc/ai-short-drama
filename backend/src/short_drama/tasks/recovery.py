"""Recover interrupted actions and lost idempotent saves, never republish generation."""

from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update

from short_drama.dao.task_runtime_dao import (
    finish,
    latest_record,
    publication_failed,
    schedule,
)
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.base import utcnow
from short_drama.tasks.state import TERMINAL, archive_due, recovery_action


def recover(factory, settings, limit=100):
    recovered = 0
    now = utcnow()
    with factory.begin() as session:
        tasks = session.scalars(
            select(AsyncTask)
            .where(
                or_(
                    and_(
                        AsyncTask.message_status.in_(("publishing", "idle")),
                        AsyncTask.locked_until <= now,
                    ),
                    and_(
                        AsyncTask.message_status == "published",
                        AsyncTask.next_action == "save",
                        AsyncTask.lock_token.is_(None),
                        AsyncTask.locked_until.is_(None),
                        AsyncTask.updated_at
                        <= now - timedelta(seconds=settings.generation_lease_seconds),
                    ),
                ),
                AsyncTask.status.not_in(TERMINAL),
            )
            .order_by(AsyncTask.locked_until)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for task in tasks:
            if task.message_status == "published":
                record = latest_record(session, task.id)
                response = (record.response_data or {}) if record else {}
                # A provider result plus durable media evidence permits save-only
                # redelivery. The new version invalidates the old message and the
                # worker's ownership guard/idempotent archive prevent duplicates.
                if (
                    record is None
                    or record.status != "succeeded"
                    or not any(
                        item.get("locator") or item.get("source_cipher")
                        for item in response.get("media_manifest", [])
                    )
                ):
                    continue
                budget = record.config_snapshot.get(
                    "archive_budget_seconds", settings.generation_archive_budget_seconds
                )
                if archive_due(response, now, budget):
                    schedule(task, "save")
                else:
                    finish(
                        task,
                        "failed",
                        {"code": "archive_timeout", "message": "结果保存窗口已过期，可恢复保存"},
                    )
            elif task.message_status == "publishing":
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
