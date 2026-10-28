"""本人转写与声音分析任务，冻结输入并使用成片任务租约。"""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.db.access.identity import scoped_key
from short_drama.domain import AIModelConfig, MediaFile, ProjectRenderJob
from short_drama.schemas.creation.assembly.editing.editor import (
    AssemblyMediaJobRead,
    AudioAnalysisCreate,
    TranscriptionCreate,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas.tools.transcription.cloud_transcription import (
    require_cloud_transcription,
    supports_cloud_transcription,
)
from short_drama.service.creation.assembly.editing.associations import picture_spans
from short_drama.service.creation.assembly.editing.editor import (
    AssemblyEditorService,
    voice_context,
)
from short_drama.utils.json_digest import digest


class AssemblyMediaService(AssemblyEditorService):
    def media_jobs(self, project_id):
        with self._transaction(read_only=True):
            _, assembly = self._scope(project_id, for_update=False)
            actor = self.session.info.get("actor")
            query = select(ProjectRenderJob).where(
                ProjectRenderJob.assembly_id == assembly.id,
                ProjectRenderJob.kind.in_(["transcription", "audio_analysis"]),
            )
            if actor:
                query = query.where(ProjectRenderJob.initiated_by == actor.user_id)
            return {
                "items": [
                    self._task_read(job)
                    for job in self.session.scalars(
                        query.order_by(ProjectRenderJob.id.desc()).limit(40)
                    )
                ]
            }

    def _task_read(self, job):
        base = self._job_read(job)
        return AssemblyMediaJobRead.model_validate(
            {
                **{
                    key: base[key]
                    for key in ("id", "kind", "status", "stage", "progress", "can_cancel", "error")
                },
                "result": deepcopy(job.manifest),
                "can_retry": job.status in {"failed", "cancelled"}
                and (
                    not (job.manifest or {}).get("submitted")
                    or (job.manifest or {}).get("reconciled", False)
                ),
            }
        ).model_dump(mode="json")

    def transcription_models(self, project_id):
        with self._transaction(read_only=True):
            self._scope(project_id, required=False, for_update=False)
            configs = self.session.scalars(
                select(AIModelConfig).where(
                    AIModelConfig.service_type == "audio",
                    AIModelConfig.enabled == 1,
                    AIModelConfig.is_deleted == 0,
                )
            )
            return {
                "local_available": bool(self.settings.canvas_whisper_base_url.strip()),
                "models": [
                    {"id": str(c.id), "name": c.name}
                    for c in configs
                    if supports_cloud_transcription(c)
                ],
            }

    def create_media_job(self, project_id, payload, key, kind):
        model = TranscriptionCreate if kind == "transcription" else AudioAnalysisCreate
        data = model.model_validate(payload)
        key = scoped_key(self.session, key)
        request_hash = digest(data.model_dump(mode="json"))
        with self._transaction():
            creation, assembly = self._scope(project_id)
            previous = self.session.scalar(
                select(ProjectRenderJob).where(
                    ProjectRenderJob.assembly_id == assembly.id,
                    ProjectRenderJob.kind == kind,
                    ProjectRenderJob.idempotency_key == key,
                )
            )
            if previous:
                if previous.request_hash != request_hash:
                    raise Conflict("此任务标识已用于其他输入")
                return self._task_read(previous)
            if kind == "transcription":
                self._version(assembly, data.row_version)
                snapshot = self._snapshot(assembly, self._clips(assembly))
                if not snapshot["clips"]:
                    raise WorkflowError("transcription_empty", "请先添加可播放的画面片段", 422)
                if any(c["trim_out_ms"] is None for c in snapshot["clips"]):
                    raise WorkflowError("transcription_empty", "请等待视频检测完成再识别", 422)
                ranges = [
                    {
                        "clip_id": clip["clip_id"],
                        "start_ms": start,
                        "end_ms": end,
                        "source_in_ms": clip["trim_in_ms"],
                    }
                    for clip, start, end in picture_spans(snapshot).values()
                ]
                cursor = ranges[-1]["end_ms"]
                chosen = (
                    next((r for r in ranges if r["clip_id"] == data.clip_id), None)
                    if data.clip_id
                    else None
                )
                if data.clip_id and not chosen:
                    raise Conflict("待识别的片段已不在时间轴中")
                snapshot["transcription"] = {
                    **data.model_dump(mode="json"),
                    "ranges": ranges,
                    "start_ms": chosen["start_ms"] if chosen else 0,
                    "end_ms": chosen["end_ms"] if chosen else cursor,
                    "source_hash": digest(snapshot),
                    "voice_context_hash": voice_context(snapshot),
                }
                if data.model_config_id:
                    actor = self.session.info.get("actor")
                    previous_cloud = self.session.scalars(
                        select(ProjectRenderJob).where(
                            ProjectRenderJob.assembly_id == assembly.id,
                            ProjectRenderJob.kind == "transcription",
                            ProjectRenderJob.initiated_by == (actor.user_id if actor else None),
                        )
                    )
                    for task in previous_cloud:
                        if not task.snapshot.get("transcription", {}).get("model_config_id"):
                            continue
                        if task.status in {"queued", "running"} or (
                            (task.manifest or {}).get("submitted")
                            and not (task.manifest or {}).get("reconciled")
                            and task.status != "succeeded"
                        ):
                            raise WorkflowError(
                                "assembly_transcription_unresolved",
                                "已有云端识别正在处理或受理结果待核实，请在识别记录中处理原任务",
                                409,
                            )
                    config = require_cloud_transcription(
                        self._require(AIModelConfig, data.model_config_id)
                    )
                    snapshot["transcription"]["model_version"] = str(config.row_version)
                elif not self.settings.canvas_whisper_base_url.strip():
                    raise WorkflowError(
                        "transcription_unconfigured",
                        "本地 Whisper 未配置，请配置服务或主动选择云端模型",
                        422,
                    )
            else:
                media = self._require(MediaFile, data.media_id)
                if media.project_id != creation.id or not media.format_code.startswith(
                    ("audio/", "video/")
                ):
                    raise WorkflowError("audio_analysis_scope", "请先导入当前项目的音视频素材", 422)
                duration = media.duration_ms or (media.video_metadata or {}).get("duration_ms")
                end = data.trim_out_ms or duration
                if not duration or not end or not data.trim_in_ms < end <= duration:
                    raise WorkflowError("audio_analysis_range", "声音分析范围无效", 422)
                snapshot = {
                    "media": [
                        {
                            "id": str(media.id),
                            "locator": media.storage_locator,
                            "checksum": media.checksum_sha256,
                        }
                    ],
                    "analysis": {
                        "media_id": str(media.id),
                        "trim_in_ms": data.trim_in_ms,
                        "trim_out_ms": end,
                    },
                }
                actor = self.session.info.get("actor")
                cached = self.session.scalar(
                    select(ProjectRenderJob)
                    .where(
                        ProjectRenderJob.assembly_id == assembly.id,
                        ProjectRenderJob.kind == kind,
                        ProjectRenderJob.context_hash == digest(snapshot),
                        ProjectRenderJob.status.in_(["queued", "running", "succeeded"]),
                        ProjectRenderJob.initiated_by == (actor.user_id if actor else None),
                    )
                    .limit(1)
                )
                if cached:
                    return self._task_read(cached)
            job = self._new_job(assembly, kind, snapshot, key, request_hash)
            return self._task_read(job)

    def media_job(self, project_id, job_id, action="get"):
        with self._transaction(read_only=action == "get"):
            _, assembly = self._scope(project_id, for_update=action != "get")
            job = self.session.get(ProjectRenderJob, int(job_id), with_for_update=action != "get")
            if (
                not job
                or job.assembly_id != assembly.id
                or job.kind not in {"transcription", "audio_analysis"}
                or (
                    self.session.info.get("actor")
                    and job.initiated_by != self.session.info["actor"].user_id
                )
            ):
                raise NotFound("媒体处理任务不存在")
            if action == "cancel" and job.status in {"queued", "running"}:
                job.cancel_requested = 1
                if job.status == "queued":
                    job.status, job.stage = "cancelled", "cancelled"
            if action == "acknowledge":
                if job.kind != "transcription" or job.status not in {"failed", "cancelled"}:
                    raise Conflict("请等待原识别任务结束后再核实")
                job.manifest = {
                    **(job.manifest or {}),
                    "reconciled": True,
                    "reconciled_at": utcnow().isoformat(),
                }
            return self._task_read(job)
