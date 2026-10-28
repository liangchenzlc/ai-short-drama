from sqlalchemy import select

from short_drama.dao.generations.runtime.lifecycle import latest_record
from short_drama.domain import (
    AIModelConfig,
    Asset,
    AsyncTask,
    GenerationBatchJob,
    MediaFile,
    ShotScript,
)
from short_drama.service.base import utcnow
from short_drama.service.generations.batches.admission import AdmissionOperations
from short_drama.service.generations.batches.base import batch_status as batch_status
from short_drama.service.generations.batches.base import item_status as item_status
from short_drama.service.generations.batches.controls import ControlsOperations
from short_drama.service.generations.batches.preflight import PreflightOperations
from short_drama.service.generations.batches.queries import QueriesOperations
from short_drama.service.generations.runtime.lifecycle import finish


class GenerationBatchService(
    PreflightOperations, AdmissionOperations, QueriesOperations, ControlsOperations
):
    """组合独立职责，共享同一 Session；事务仍由原业务操作完整持有。"""


def dispatch_batches(factory, settings):
    """One transaction per model serializes slots across schedulers and batches."""
    if not settings.generation_batches_enabled:
        return 0
    dispatched = 0
    with factory() as session:
        configs = session.scalars(
            select(GenerationBatchJob.config_id)
            .where(
                GenerationBatchJob.status.in_(("running", "paused", "needs_review", "cancelled"))
            )
            .distinct()
        ).all()
    for config_id in configs:
        with factory.begin() as session:
            from short_drama.domain import Project

            # API transactions and revocation start with the project lock.
            project_ids = session.scalars(
                select(GenerationBatchJob.project_id)
                .where(
                    GenerationBatchJob.config_id == config_id,
                    GenerationBatchJob.project_id.is_not(None),
                )
                .distinct()
            ).all()
            for project_id in sorted(project_ids):
                session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            config = session.scalar(
                select(AIModelConfig).where(AIModelConfig.id == config_id).with_for_update()
            )
            if config is None:
                continue
            svc = GenerationBatchService(session, settings)
            batches = list(
                session.scalars(
                    select(GenerationBatchJob)
                    .where(
                        GenerationBatchJob.config_id == config_id,
                        GenerationBatchJob.status.in_(
                            ("running", "paused", "needs_review", "cancelled")
                        ),
                    )
                    .order_by(GenerationBatchJob.id)
                    .with_for_update()
                )
            )
            groups = [(batch, svc._reconcile(batch)) for batch in batches]
            active = sum(
                item.status in {"active", "needs_review"} for _, items in groups for item in items
            )
            cap = getattr(settings, f"generation_batch_{config.service_type}_concurrency")
            for batch, items in groups:
                if batch.status != "running":
                    continue
                for item in items:
                    if item.status != "waiting" or active >= cap:
                        continue
                    task = svc._require(AsyncTask, item.task_id)
                    record = latest_record(session, task.id)
                    from short_drama.service.generations.runtime.access import may_submit

                    if not may_submit(session, task):
                        finish(task, "cancelled", {"code": "access_revoked"})
                        item.status = "cancelled"
                        continue
                    references = (record.request_data.get("input") or {}).get(
                        "reference_media_ids", []
                    )
                    inputs = record.request_data.get("input") or {}
                    references = references + inputs.get("audio_reference_media_ids", [])
                    references = references + [
                        inputs[k]
                        for k in ("first_frame_media_id", "last_frame_media_id")
                        if inputs.get(k)
                    ]
                    available = all(session.get(MediaFile, int(i)) is not None for i in references)
                    source = session.get(
                        Asset if batch.scene == "asset_image" else ShotScript, item.source_id
                    )
                    native_changed = False
                    if source is not None and batch.scene == "shot_video":
                        from short_drama.service.creation.sound.voices.context import native_context

                        frozen = (record.request_data.get("source_snapshot") or {}).get(
                            "native_speech"
                        )
                        native_changed = frozen != native_context(
                            session, source, settings=settings
                        )
                    if (
                        not config.enabled
                        or config.is_deleted
                        or config.row_version != batch.config_version
                        or not available
                        or native_changed
                        or source is None
                        or getattr(source, "deleted_at", None)
                        or str(source.row_version)
                        != str(record.request_data.get("source", {}).get("row_version"))
                    ):
                        item.status = "blocked"
                        item.error = {
                            "code": "batch_dependency_unavailable",
                            "message": "模型、来源或参考媒体已变化，请核对后恢复或重新预检",
                        }
                        batch.status = "paused"
                        break
                    task.message_status = "pending"
                    task.next_run_at = task.updated_at = utcnow()
                    item.status = "active"
                    active += 1
                    dispatched += 1
    return dispatched
