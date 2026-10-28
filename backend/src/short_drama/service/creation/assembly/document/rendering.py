from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.access.identity import scoped_key
from short_drama.db.scope.resource_scope import require_project
from short_drama.domain import (
    MediaFile,
    ProjectRenderJob,
)
from short_drama.schemas.creation.assembly.document.assembly import (
    AssemblyApply,
    AssemblyExport,
)
from short_drama.service.base import utcnow
from short_drama.service.creation.assembly.document.base import AssemblyServiceBase
from short_drama.service.creation.storyboard.management.storyboard import (
    normalize_creation_key,
)
from short_drama.utils.json_digest import digest as digest
from short_drama.utils.snowflake import next_id


class AssemblyRendering(AssemblyServiceBase):
    def _new_job(self, assembly, kind, snapshot, key, request_hash=None, retry_of=None):
        now = utcnow()
        job = ProjectRenderJob(
            id=next_id(),
            assembly_id=assembly.id,
            kind=kind,
            status="queued",
            stage="queued",
            snapshot=snapshot,
            context_hash=digest(snapshot),
            idempotency_key=key,
            request_hash=request_hash or digest(snapshot),
            progress=0,
            attempts=0,
            message_version=1,
            next_run_at=now,
            cancel_requested=0,
            retry_of_id=retry_of,
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def export(self, project_id, payload, key, kind="export"):
        data = AssemblyExport.model_validate(payload)
        key = scoped_key(self.session, normalize_creation_key(key))
        with self._transaction():
            creation, assembly = self._scope(project_id)
            request_hash = digest(data.model_dump(mode="json"))
            previous = self.session.scalar(
                select(ProjectRenderJob).where(
                    ProjectRenderJob.assembly_id == assembly.id,
                    ProjectRenderJob.kind == kind,
                    ProjectRenderJob.idempotency_key == key,
                )
            )
            if previous:
                if previous.request_hash != request_hash:
                    raise WorkflowError(
                        "assembly_idempotency_conflict", "请求标识已被其他导出使用", 409
                    )
                return self._job_read(previous)
            self._version(assembly, data.row_version)
            read = self._read(creation, assembly)
            if read["source_hash"] != data.source_hash:
                raise WorkflowError(
                    "assembly_source_changed", "分镜来源已变化，请刷新核对后重试", 409
                )
            included = [c for c in read["clips"] if c["included"] and c["media_id"] is not None]
            if not included or any(c["issue"] for c in included):
                raise WorkflowError(
                    "assembly_not_ready", "请补齐视频、等待检测完成并检查裁剪范围后导出", 422
                )
            if any(c["is_stale"] for c in included) and not data.acknowledge_stale_source:
                raise WorkflowError(
                    "assembly_stale_source", "部分视频对应旧分镜，请核对后确认使用", 409
                )
            snapshot = self._snapshot(assembly, self._clips(assembly))
            if snapshot["output_status"] != "ready":
                raise WorkflowError(
                    "assembly_output_not_ready", "视频显示尺寸尚未就绪，请完成检测后导出", 422
                )
            from short_drama.service.creation.sound.tracks.snapshot import validate_sound_snapshot

            validate_sound_snapshot(snapshot)
            if kind == "preview":
                # Preview uses the same edit decisions and frame rate as export.
                cached = self.session.scalar(
                    select(ProjectRenderJob)
                    .where(
                        ProjectRenderJob.assembly_id == assembly.id,
                        ProjectRenderJob.kind == "preview",
                        ProjectRenderJob.context_hash == digest(snapshot),
                        ProjectRenderJob.cancel_requested == 0,
                        ProjectRenderJob.status.in_(["queued", "running", "succeeded"]),
                    )
                    .order_by(ProjectRenderJob.created_at.desc())
                    .limit(1)
                )
                if cached:
                    return self._job_read(cached)
                obsolete = self.session.scalars(
                    select(ProjectRenderJob).where(
                        ProjectRenderJob.assembly_id == assembly.id,
                        ProjectRenderJob.kind == "preview",
                        ProjectRenderJob.status.in_(["queued", "running"]),
                    )
                )
                for job in obsolete:
                    job.cancel_requested = 1
                    if job.status == "queued":
                        job.status, job.stage, job.finished_at = "cancelled", "cancelled", utcnow()
            total = sum(c["trim_out_ms"] - c["trim_in_ms"] for c in snapshot["clips"])
            if total > getattr(self.settings, "render_max_duration_ms", 3600000):
                raise WorkflowError("assembly_limit", "成片总时长超过当前导出上限", 422)
            return self._job_read(self._new_job(assembly, kind, snapshot, key, request_hash))

    def job_action(self, project_id, job_id, action, payload=None, key=None):
        read_only = action == "get"
        with self._transaction(read_only=read_only):
            creation, assembly = self._scope(project_id, for_update=not read_only)
            statement = select(ProjectRenderJob).where(
                ProjectRenderJob.id == int(job_id), ProjectRenderJob.assembly_id == assembly.id
            )
            if not read_only:
                statement = statement.with_for_update()
            job = self.session.scalar(statement)
            if not job:
                raise NotFound("导出任务不存在")
            actor = self.session.info.get("actor")
            if actor and action in {"cancel", "retry"} and job.initiated_by != actor.user_id:
                if action == "cancel":
                    require_project(self.session, project_id, owner=True)
                else:
                    raise WorkflowError(
                        "task_actor_required", "Only the task initiator can retry", 403
                    )
            if action == "cancel" and job.status in ("queued", "running"):
                job.cancel_requested = 1
                if job.status == "queued":
                    job.status, job.stage, job.finished_at = "cancelled", "cancelled", utcnow()
            elif action == "retry":
                key = scoped_key(self.session, normalize_creation_key(key))
                previous = self.session.scalar(
                    select(ProjectRenderJob).where(
                        ProjectRenderJob.assembly_id == assembly.id,
                        ProjectRenderJob.kind == job.kind,
                        ProjectRenderJob.idempotency_key == key,
                    )
                )
                if previous:
                    if previous.retry_of_id != job.id:
                        raise WorkflowError(
                            "assembly_idempotency_conflict", "请求标识已被其他导出使用", 409
                        )
                    return self._job_read(previous)
                if job.status not in ("failed", "cancelled"):
                    raise WorkflowError("assembly_job_state", "仅失败或取消的任务可以重试", 409)
                job = self._new_job(assembly, job.kind, job.snapshot, key, retry_of=job.id)
            elif action == "apply":
                data = AssemblyApply.model_validate(payload)
                self._version(assembly, data.row_version)
                current = digest(self._snapshot(assembly, self._clips(assembly)))
                if data.context_hash != current:
                    raise WorkflowError(
                        "assembly_version_conflict", "成片草稿已变化，请刷新后重试", 409
                    )
                if current != job.context_hash and not data.acknowledge_stale_source:
                    raise WorkflowError(
                        "assembly_stale_source", "此成片与当前草稿不同，请确认采用旧版本", 409
                    )
                if job.kind != "export" or job.status != "succeeded" or not job.output_media_id:
                    raise WorkflowError("assembly_job_state", "任务尚未成功完成", 409)
                assembly.current_media_id = job.output_media_id
                from short_drama.service.creation.writing.script.publication import publish

                media = publish(self._require(MediaFile, job.output_media_id))
                media.video_metadata = {
                    **(media.video_metadata or {}),
                    "publication_context_hash": job.context_hash,
                }
                self._touch(assembly)
            return self._job_read(
                job,
                digest(self._snapshot(assembly, self._clips(assembly), for_update=not read_only)),
            )
