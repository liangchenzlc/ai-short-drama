"""Versioned edit decisions and immutable local-render requests."""

import hashlib
import json

from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.access import require_project, scope_of, scoped_key
from short_drama.domain import (
    Episode,
    EpisodeAssembly,
    EpisodeAssemblyClip,
    EpisodeRenderJob,
    MediaFile,
    ShotScript,
)
from short_drama.schemas.episode_assembly import (
    AssemblyApply,
    AssemblyEdit,
    AssemblyExport,
    AssemblyVersion,
)
from short_drama.service.base import BaseService, utcnow
from short_drama.service.episode_storyboard_service import (
    EpisodeStoryboardService,
    normalize_creation_key,
)
from short_drama.service.shot_video_context import DEFAULT_VIDEO_SETTINGS, video_context_hash
from short_drama.service.storage_service import StorageService
from short_drama.utils.snowflake import next_id


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class EpisodeAssemblyService(BaseService):
    model = EpisodeAssembly

    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.settings, self.storage = settings, storage
        self.storyboard = EpisodeStoryboardService(session, settings, storage)

    def _scope(self, project_id, episode_id, required=True):
        episode = self.storyboard.lock_episode(project_id, episode_id)
        assembly = self.session.scalar(
            select(EpisodeAssembly)
            .where(EpisodeAssembly.episode_id == episode.id)
            .with_for_update()
        )
        if required and assembly is None:
            raise NotFound("尚未创建成片草稿")
        return episode, assembly

    @staticmethod
    def _clip_key(clip):
        return clip.client_key or str(clip.id)

    def _clips(self, assembly, include_removed=False):
        return list(
            self.session.scalars(
                select(EpisodeAssemblyClip)
                .where(EpisodeAssemblyClip.assembly_id == assembly.id)
                .where(True if include_removed else EpisodeAssemblyClip.removed == 0)
                .order_by(EpisodeAssemblyClip.position)
            )
        )

    def _sources(self, episode):
        shots = list(
            self.session.scalars(
                select(ShotScript)
                .where(ShotScript.episode_id == episode.id)
                .order_by(ShotScript.position, ShotScript.id)
            )
        )
        ids = [s.id for s in shots]
        assets, images = self.storyboard.dao.list_details(ids)
        videos = self.storyboard.dao.video_rows(ids)
        result = {}
        for shot in shots:
            _, context = self.storyboard._context(episode, shot, assets[shot.id])
            image = images.get(shot.id)
            current_hash = video_context_hash(
                context,
                str(image[1].id) if image else None,
                shot.video_prompt or "",
                shot.video_settings or DEFAULT_VIDEO_SETTINGS,
                session=self.session,
                shot=shot,
            )
            video = videos.get(shot.id)
            result[shot.id] = {
                "shot_id": str(shot.id),
                "script": shot.script,
                "position": shot.position,
                "archived": shot.deleted_at is not None,
                "media_id": str(video[1].id) if video else None,
                "context_hash": video[0].context_hash if video else None,
                "current_hash": current_hash,
                "poster": self._url(image[1]) if image else None,
            }
        return result

    @staticmethod
    def _source_hash(sources, episode):
        return digest(
            {
                "aspect": episode.aspect,
                "sources": [
                    {k: v for k, v in s.items() if k != "poster"} for s in sources.values()
                ],
            }
        )

    def _url(self, media):
        return (
            StorageService(self.storage, self.settings).download_url(media.storage_locator)
            if media and self.storage and self.settings
            else None
        )

    def _derived_url(self, metadata, key):
        locator = (metadata or {}).get(key)
        return (
            StorageService(self.storage, self.settings).download_url(locator)
            if locator and self.storage and self.settings
            else None
        )

    @staticmethod
    def _version(assembly, version):
        if assembly.row_version != int(version):
            raise WorkflowError(
                "assembly_version_conflict", "成片草稿已在其他窗口修改，请保留草稿后重新载入", 409
            )

    def _source_version(self, episode, sources, expected):
        if self._source_hash(sources, episode) != expected:
            raise WorkflowError("assembly_source_changed", "分镜来源已变化，请刷新核对后重试", 409)

    @staticmethod
    def _touch(assembly):
        assembly.row_version += 1
        assembly.updated_at = utcnow()

    def _snapshot(self, assembly, clips):
        snapshot = self._video_snapshot(assembly, clips)
        if getattr(self.settings, "audio_production_enabled", False):
            from .episode_sound_service import EpisodeSoundService

            sound = EpisodeSoundService(self.session, self.settings, self.storage).snapshot(
                assembly, snapshot
            )
            if sound is not None:
                snapshot = {**snapshot, "version": 3, "sound": sound}
        return snapshot

    def _video_snapshot(self, assembly, clips):
        from .native_voice_service import project_mode

        episode = self.session.get(Episode, assembly.episode_id)
        native = project_mode(self.session, episode.project_id, self.settings) == "native"
        media_ids = [c.media_id for c in clips if c.media_id]
        media = {
            m.id: m
            for m in self.session.scalars(select(MediaFile).where(MediaFile.id.in_(media_ids)))
        }
        items = []
        for c in clips:
            # Empty shots remain in the draft and participate after video is synced.
            if not c.included or c.media_id is None:
                continue
            m = media.get(c.media_id)
            metadata = m.video_metadata if m else None
            duration = metadata.get("duration_ms") if metadata else None
            end = c.trim_out_ms if c.trim_out_ms is not None else duration
            items.append(
                {
                    "clip_id": self._clip_key(c),
                    "shot_id": str(c.shot_id),
                    "media_id": str(c.media_id) if c.media_id else None,
                    "locator": m.storage_locator if m else None,
                    "checksum": m.checksum_sha256 if m else None,
                    "trim_in_ms": c.trim_in_ms,
                    "trim_out_ms": end,
                    "muted": bool(c.muted),
                    "duration_ms": duration,
                    "source_context_hash": c.source_context_hash,
                }
            )
        return {
            "version": 2,
            **({"sound_mode": "native"} if native else {}),
            "aspect": assembly.aspect,
            "resolution": assembly.resolution,
            "fps": 30,
            "clips": items,
        }

    def _job_read(self, job, current_hash=None):
        actor = self.session.info.get("actor")
        own_job = actor is None or job.initiated_by == actor.user_id
        can_cancel = own_job
        if actor and not own_job:
            from short_drama.domain import Project

            project_id = scope_of(self.session, job)[1]
            can_cancel = (
                self.session.scalar(select(Project.owner_user_id).where(Project.id == project_id))
                == actor.user_id
            )
        media = self.session.get(MediaFile, job.output_media_id) if job.output_media_id else None
        clips = job.snapshot.get("clips", [])
        source_ids = {int(c["media_id"]) for c in clips if c.get("media_id")}
        sources = {
            m.id: m
            for m in self.session.scalars(select(MediaFile).where(MediaFile.id.in_(source_ids)))
        }
        timeline = []
        for clip in clips:
            source = sources.get(int(clip["media_id"])) if clip.get("media_id") else None
            metadata = source.video_metadata if source else None
            timeline.append(
                {
                    **{
                        k: clip[k]
                        for k in ("clip_id", "shot_id", "trim_in_ms", "trim_out_ms", "muted")
                    },
                    "media_id": clip.get("media_id"),
                    "duration_ms": clip.get("duration_ms") or (metadata or {}).get("duration_ms"),
                    "url": self._derived_url(metadata, "preview_locator") or self._url(source),
                    "poster": self._derived_url(metadata, "thumbnail_locator"),
                    "filmstrip": {
                        "url": self._derived_url(metadata, "filmstrip_locator"),
                        "count": metadata["filmstrip_count"],
                        "interval_ms": metadata["filmstrip_interval_ms"],
                    }
                    if metadata
                    and metadata.get("filmstrip_locator")
                    and metadata.get("filmstrip_count")
                    and metadata.get("filmstrip_interval_ms")
                    else None,
                }
            )
        return {
            "id": str(job.id),
            "kind": job.kind,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "cancel_requested": bool(job.cancel_requested),
            "initiated_by": str(job.initiated_by) if job.initiated_by else None,
            "can_cancel": can_cancel and job.status in {"queued", "running"},
            "can_retry": own_job and job.status in {"failed", "cancelled"},
            "error": job.error,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "context_hash": job.context_hash,
            "aspect": job.snapshot.get("aspect"),
            "resolution": job.snapshot.get("resolution"),
            "url": self._url(media),
            "media_id": str(media.id) if media else None,
            "duration_ms": media.duration_ms if media else None,
            "is_stale": current_hash is not None and current_hash != job.context_hash,
            "timeline": timeline,
        }

    def _read(self, episode, assembly, sources=None):
        sources = sources if sources is not None else self._sources(episode)
        source_hash = self._source_hash(sources, episode)
        if assembly is None:
            return {
                "assembly": None,
                "source_hash": source_hash,
                "source_count": sum(
                    not s["archived"] and s["media_id"] is not None for s in sources.values()
                ),
            }
        clips = self._clips(assembly, include_removed=True)
        media = {
            m.id: m
            for m in self.session.scalars(
                select(MediaFile).where(MediaFile.id.in_([c.media_id for c in clips if c.media_id]))
            )
        }
        values, changes = [], []
        known = {c.shot_id for c in clips}
        for c in clips:
            source = sources[c.shot_id]
            m = media.get(c.media_id)
            metadata = m.video_metadata if m else None
            changed = source["media_id"] != (str(c.media_id) if c.media_id else None)
            stale = (
                source["archived"]
                or changed
                or not c.source_context_hash
                or c.source_context_hash != source["current_hash"]
            )
            issue = (
                "missing"
                if m is None
                else "invalid"
                if metadata and metadata.get("error")
                else "preparing"
                if not metadata
                else None
            )
            duration = metadata.get("duration_ms") if metadata else None
            if duration and (
                c.trim_in_ms >= duration or (c.trim_out_ms is not None and c.trim_out_ms > duration)
            ):
                issue = "trim"
            values.append(
                {
                    "id": self._clip_key(c),
                    "removed": bool(c.removed),
                    "shot_id": str(c.shot_id),
                    "media_id": str(c.media_id) if c.media_id else None,
                    "position": c.position,
                    "included": bool(c.included),
                    "muted": bool(c.muted),
                    "trim_in_ms": c.trim_in_ms,
                    "trim_out_ms": c.trim_out_ms,
                    "duration_ms": duration,
                    "script": source["script"],
                    "shot_position": source["position"],
                    "url": self._derived_url(metadata, "preview_locator") or self._url(m),
                    "poster": self._derived_url(metadata, "thumbnail_locator") or source["poster"],
                    "filmstrip": {
                        "url": self._derived_url(metadata, "filmstrip_locator"),
                        "count": metadata["filmstrip_count"],
                        "interval_ms": metadata["filmstrip_interval_ms"],
                    }
                    if metadata
                    and metadata.get("filmstrip_locator")
                    and metadata.get("filmstrip_count")
                    and metadata.get("filmstrip_interval_ms")
                    else None,
                    "is_stale": stale,
                    "issue": issue,
                    "archived": source["archived"],
                }
            )
            if changed or (source["archived"] and c.included):
                changes.append(
                    {
                        "shot_id": str(c.shot_id),
                        "position": source["position"],
                        "kind": "archived" if source["archived"] else "replacement",
                    }
                )
        for sid, source in sources.items():
            if sid not in known and not source["archived"]:
                changes.append(
                    {"shot_id": str(sid), "position": source["position"], "kind": "added"}
                )
        active_clips = [c for c in clips if not c.removed]
        context_hash = digest(self._snapshot(assembly, active_clips))
        library = {}
        for item in values:
            library.setdefault(
                item["shot_id"],
                {
                    **item,
                    "trim_in_ms": 0,
                    "trim_out_ms": None,
                    "included": True,
                    "muted": False,
                    "issue": None if item["issue"] == "trim" else item["issue"],
                },
            )
        jobs = list(
            self.session.scalars(
                select(EpisodeRenderJob)
                .where(EpisodeRenderJob.assembly_id == assembly.id)
                .order_by(EpisodeRenderJob.created_at.desc(), EpisodeRenderJob.id.desc())
                .limit(30)
            )
        )
        # Frequent composite previews must not hide the last playable export.
        latest_export = self.session.scalar(
            select(EpisodeRenderJob)
            .where(
                EpisodeRenderJob.assembly_id == assembly.id,
                EpisodeRenderJob.kind == "export",
                EpisodeRenderJob.status == "succeeded",
            )
            .order_by(EpisodeRenderJob.created_at.desc(), EpisodeRenderJob.id.desc())
            .limit(1)
        )
        if latest_export and not any(j.id == latest_export.id for j in jobs):
            jobs.append(latest_export)
        if assembly.current_media_id and not any(
            j.output_media_id == assembly.current_media_id for j in jobs
        ):
            adopted = self.session.scalar(
                select(EpisodeRenderJob).where(
                    EpisodeRenderJob.assembly_id == assembly.id,
                    EpisodeRenderJob.output_media_id == assembly.current_media_id,
                    EpisodeRenderJob.status == "succeeded",
                )
            )
            if adopted:
                jobs.append(adopted)
        return {
            "assembly": {
                "id": str(assembly.id),
                "row_version": str(assembly.row_version),
                "aspect": assembly.aspect,
                "resolution": assembly.resolution,
                "current_media_id": str(assembly.current_media_id)
                if assembly.current_media_id
                else None,
            },
            "clips": [c for c in values if not c["removed"]],
            "sources": list(library.values()),
            "source_hash": source_hash,
            "context_hash": context_hash,
            "changes": list({(c["shot_id"], c["kind"]): c for c in changes}.values()),
            "jobs": [self._job_read(j, context_hash) for j in jobs],
        }

    def get(self, project_id, episode_id):
        with self._transaction():
            episode, assembly = self._scope(project_id, episode_id, False)
            return self._read(episode, assembly)

    def _new_job(self, assembly, kind, snapshot, key, request_hash=None, retry_of=None):
        now = utcnow()
        job = EpisodeRenderJob(
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

    def _probe(self, assembly):
        clips = self._clips(assembly, include_removed=True)
        ids = sorted({c.media_id for c in clips if c.media_id})
        pending = [
            m
            for m in self.session.scalars(select(MediaFile).where(MediaFile.id.in_(ids)))
            if not m.video_metadata
            or m.video_metadata.get("error")
            or not m.video_metadata.get("preview_locator")
            or not m.video_metadata.get("filmstrip_locator")
        ]
        if pending:
            active = self.session.scalars(
                select(EpisodeRenderJob).where(
                    EpisodeRenderJob.assembly_id == assembly.id,
                    EpisodeRenderJob.kind == "probe",
                    EpisodeRenderJob.status.in_(["queued", "running"]),
                    EpisodeRenderJob.cancel_requested == 0,
                )
            )
            covered = {
                str(entry["id"]) for job in active for entry in job.snapshot.get("media", [])
            }
            pending = [m for m in pending if str(m.id) not in covered]
            if pending:
                snapshot = {
                    "media": [{"id": str(m.id), "locator": m.storage_locator} for m in pending]
                }
                self._new_job(assembly, "probe", snapshot, str(next_id()))

    def initialize(self, project_id, episode_id):
        with self._transaction():
            episode, assembly = self._scope(project_id, episode_id, False)
            sources = self._sources(episode)
            if assembly is None:
                if sum(not s["archived"] for s in sources.values()) > 300:
                    raise WorkflowError("assembly_limit", "单集成片最多支持300个镜头", 422)
                now = utcnow()
                assembly = EpisodeAssembly(
                    id=next_id(),
                    episode_id=episode.id,
                    aspect=episode.aspect,
                    resolution="720p",
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(assembly)
                self.session.flush()
                self._sync(assembly, sources)
            self._probe(assembly)
            return self._read(episode, assembly, sources)

    def _sync(self, assembly, sources):
        clips = self._clips(assembly, include_removed=True)
        by_shot = {}
        for c in clips:
            by_shot.setdefault(c.shot_id, []).append(c)
        added = [sid for sid, s in sources.items() if not s["archived"] and sid not in by_shot]
        if sum(not c.removed for c in clips) + len(added) > 300:
            raise WorkflowError("assembly_limit", "单集成片最多支持300个镜头", 422)
        for sid, source in sources.items():
            group = by_shot.get(sid, [])
            if source["archived"]:
                for c in group:
                    c.included = 0
                continue
            if not group:
                c = EpisodeAssemblyClip(
                    id=next_id(),
                    assembly_id=assembly.id,
                    shot_id=sid,
                    position=max((item.position for item in clips), default=0) + 1,
                    included=1,
                    muted=0,
                    trim_in_ms=0,
                    removed=0,
                )
                self.session.add(c)
                clips.append(c)
                group = [c]
            mid = int(source["media_id"]) if source["media_id"] else None
            for c in group:
                # Explicit replacement preserves split boundaries. A shorter source
                # is flagged for correction rather than silently destroying edits.
                c.media_id = mid
                c.source_context_hash = source["context_hash"]
        self.session.flush()

    def sync(self, project_id, episode_id, payload):
        data = AssemblyVersion.model_validate(payload)
        with self._transaction():
            episode, assembly = self._scope(project_id, episode_id)
            self._version(assembly, data.row_version)
            sources = self._sources(episode)
            self._source_version(episode, sources, data.source_hash)
            self._sync(assembly, sources)
            assembly.aspect = episode.aspect
            self._touch(assembly)
            self._probe(assembly)
            return self._read(episode, assembly, sources)

    def edit(self, project_id, episode_id, payload):
        data = AssemblyEdit.model_validate(payload)
        with self._transaction():
            episode, assembly = self._scope(project_id, episode_id)
            fingerprint = digest(data.model_dump(mode="json"))
            receipt = assembly.last_edit_receipt or {}
            if data.request_id and receipt.get("key") == data.request_id:
                if receipt.get("hash") != fingerprint:
                    raise WorkflowError(
                        "assembly_idempotency_conflict", "保存请求标识已被其他编辑使用", 409
                    )
                return self._read(episode, assembly)
            self._version(assembly, data.row_version)
            clips = self._clips(assembly, include_removed=True)
            by_id = {self._clip_key(c): c for c in clips}
            # Move all rows beyond the current position range before rearranging.
            # Removed rows are retained for undo and to remember imported sources.
            offset = max((c.position for c in clips), default=0) + 301
            for i, c in enumerate(clips, 1):
                c.position = offset + i
                c.removed = 1
            self.session.flush()
            for position, edit in enumerate(data.clips, 1):
                c = by_id.get(edit.id)
                if c is None:
                    original = by_id.get(edit.source_clip_id)
                    if original is None or "-" not in edit.id:
                        raise WorkflowError("assembly_clip_set", "片段来源不存在或不属于本集", 422)
                    c = EpisodeAssemblyClip(
                        id=next_id(),
                        client_key=edit.id,
                        assembly_id=assembly.id,
                        shot_id=original.shot_id,
                        media_id=original.media_id,
                        source_context_hash=original.source_context_hash,
                        position=position,
                        removed=0,
                        included=int(edit.included),
                        muted=int(edit.muted),
                        trim_in_ms=edit.trim_in_ms,
                        trim_out_ms=edit.trim_out_ms,
                    )
                    self.session.add(c)
                    by_id[edit.id] = c
                m = self.session.get(MediaFile, c.media_id) if c.media_id else None
                duration = (m.video_metadata or {}).get("duration_ms") if m else None
                if duration and (
                    edit.trim_in_ms >= duration
                    or (edit.trim_out_ms is not None and edit.trim_out_ms > duration)
                    or (
                        ((edit.trim_out_ms or duration) * 30 + 500) // 1000
                        <= (edit.trim_in_ms * 30 + 500) // 1000
                    )
                ):
                    raise WorkflowError("assembly_trim_invalid", "裁剪范围超出视频实际时长", 422)
                c.position, c.included, c.muted = position, int(edit.included), int(edit.muted)
                c.removed = 0
                c.trim_in_ms, c.trim_out_ms = edit.trim_in_ms, edit.trim_out_ms
            # Compact tombstones as well; repeated autosaves must not overflow positions.
            for position, c in enumerate((c for c in clips if c.removed), len(data.clips) + 1):
                c.position = position
            assembly.resolution = data.resolution
            assembly.last_edit_receipt = (
                {"key": data.request_id, "hash": fingerprint} if data.request_id else None
            )
            self._touch(assembly)
            self.session.flush()
            return self._read(episode, assembly)

    def export(self, project_id, episode_id, payload, key, kind="export"):
        data = AssemblyExport.model_validate(payload)
        key = scoped_key(self.session, normalize_creation_key(key))
        with self._transaction():
            episode, assembly = self._scope(project_id, episode_id)
            request_hash = digest(data.model_dump(mode="json"))
            previous = self.session.scalar(
                select(EpisodeRenderJob).where(
                    EpisodeRenderJob.assembly_id == assembly.id,
                    EpisodeRenderJob.kind == kind,
                    EpisodeRenderJob.idempotency_key == key,
                )
            )
            if previous:
                if previous.request_hash != request_hash:
                    raise WorkflowError(
                        "assembly_idempotency_conflict", "请求标识已被其他导出使用", 409
                    )
                return self._job_read(previous)
            self._version(assembly, data.row_version)
            read = self._read(episode, assembly)
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
            from .episode_sound_service import validate_sound_snapshot

            validate_sound_snapshot(snapshot)
            if kind == "preview":
                # Preview uses the same edit decisions and frame rate as export.
                cached = self.session.scalar(
                    select(EpisodeRenderJob)
                    .where(
                        EpisodeRenderJob.assembly_id == assembly.id,
                        EpisodeRenderJob.kind == "preview",
                        EpisodeRenderJob.context_hash == digest(snapshot),
                        EpisodeRenderJob.cancel_requested == 0,
                        EpisodeRenderJob.status.in_(["queued", "running", "succeeded"]),
                    )
                    .order_by(EpisodeRenderJob.created_at.desc())
                    .limit(1)
                )
                if cached:
                    return self._job_read(cached)
                obsolete = self.session.scalars(
                    select(EpisodeRenderJob).where(
                        EpisodeRenderJob.assembly_id == assembly.id,
                        EpisodeRenderJob.kind == "preview",
                        EpisodeRenderJob.status.in_(["queued", "running"]),
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

    def jobs(self, project_id, episode_id, offset=0, limit=20):
        with self._transaction():
            _, assembly = self._scope(project_id, episode_id)
            context = digest(self._snapshot(assembly, self._clips(assembly)))
            jobs = self.session.scalars(
                select(EpisodeRenderJob)
                .where(
                    EpisodeRenderJob.assembly_id == assembly.id, EpisodeRenderJob.kind == "export"
                )
                .order_by(EpisodeRenderJob.created_at.desc(), EpisodeRenderJob.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
            items = [self._job_read(j, context) for j in jobs]
            return {"items": items[:limit], "has_more": len(items) > limit, "offset": offset}

    def job_action(self, project_id, episode_id, job_id, action, payload=None, key=None):
        with self._transaction():
            episode, assembly = self._scope(project_id, episode_id)
            job = self.session.scalar(
                select(EpisodeRenderJob)
                .where(
                    EpisodeRenderJob.id == int(job_id), EpisodeRenderJob.assembly_id == assembly.id
                )
                .with_for_update()
            )
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
                    select(EpisodeRenderJob).where(
                        EpisodeRenderJob.assembly_id == assembly.id,
                        EpisodeRenderJob.kind == job.kind,
                        EpisodeRenderJob.idempotency_key == key,
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
                self._touch(assembly)
            return self._job_read(job, digest(self._snapshot(assembly, self._clips(assembly))))
