from sqlalchemy import select

from short_drama.core.exceptions import NotFound
from short_drama.db.scope.resource_scope import scope_of
from short_drama.domain import (
    MediaFile,
    Project,
    ProjectRenderJob,
)
from short_drama.schemas.creation.assembly.document.assembly import (
    AssemblyWorkRead,
)
from short_drama.service.creation.assembly.document.base import AssemblyServiceBase
from short_drama.service.creation.assembly.rendering.output import resolve_output
from short_drama.service.media.files.storage import StorageService
from short_drama.utils.json_digest import digest as digest


class AssemblyProjection(AssemblyServiceBase):
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

    def _snapshot(self, assembly, clips, *, for_update=True):
        snapshot = self._video_snapshot(assembly, clips)
        from short_drama.domain.creation.sound.tracks.sound import ProjectSound

        saved_sound = self.session.get(ProjectSound, assembly.id)
        if getattr(self.settings, "audio_production_enabled", False) or (
            saved_sound and saved_sound.document.get("schema_version", 1) >= 2
        ):
            from short_drama.service.creation.sound.tracks.snapshot import sound_snapshot

            sound = sound_snapshot(
                self.session, self.settings, assembly, snapshot, for_update=for_update
            )
            if sound is not None:
                snapshot = {**snapshot, "version": 3, "sound": sound}
        return snapshot

    def _video_snapshot(self, assembly, clips):
        from short_drama.service.creation.sound.voices.context import project_mode

        creation = self.session.get(Project, assembly.project_id)
        native = project_mode(self.session, creation.id, self.settings) == "native"
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
            duration = (
                c.display_duration_ms
                if c.source_type == "image"
                else ((metadata or {}).get("duration_ms") or m.duration_ms)
                if m
                else None
            )
            end = c.trim_out_ms if c.trim_out_ms is not None else duration
            items.append(
                {
                    "clip_id": self._clip_key(c),
                    "shot_id": str(c.shot_id) if c.shot_id else None,
                    "source_type": c.source_type,
                    "transform": c.transform,
                    "width": ((metadata or {}).get("width") or m.width) if m else None,
                    "height": ((metadata or {}).get("height") or m.height) if m else None,
                    "probe_error": (metadata or {}).get("error"),
                    "has_audio": bool((metadata or {}).get("has_audio")),
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
            **resolve_output(assembly.aspect, assembly.resolution, items),
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
            "output_width": job.snapshot.get("output_width"),
            "output_height": job.snapshot.get("output_height"),
            "output_source": job.snapshot.get("output_source"),
            "url": self._url(media),
            "media_id": str(media.id) if media else None,
            "duration_ms": media.duration_ms if media else None,
            "is_stale": current_hash is not None and current_hash != job.context_hash,
            "timeline": timeline,
        }

    def _read(self, creation, assembly, sources=None, *, for_update=True):
        sources = sources if sources is not None else self._sources(creation)
        source_hash = self._source_hash(sources, creation)
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
            source = sources.get(c.shot_id) or {
                "media_id": str(c.media_id) if c.media_id else None,
                "archived": False,
                "script": media[c.media_id].original_name if c.media_id in media else "",
                "position": c.position,
                "poster": None,
                "current_hash": c.source_context_hash,
            }
            m = media.get(c.media_id)
            metadata = m.video_metadata if m else None
            changed = source["media_id"] != (str(c.media_id) if c.media_id else None)
            stale = (
                source["archived"]
                or changed
                or (
                    c.source_type == "shot"
                    and (
                        not c.source_context_hash or c.source_context_hash != source["current_hash"]
                    )
                )
            )
            issue = (
                "missing"
                if m is None
                else "invalid"
                if metadata and metadata.get("error")
                else "preparing"
                if not metadata and c.source_type != "image"
                else None
            )
            duration = (
                c.display_duration_ms
                if c.source_type == "image"
                else (metadata or {}).get("duration_ms")
            )
            if duration and (
                c.trim_in_ms >= duration or (c.trim_out_ms is not None and c.trim_out_ms > duration)
            ):
                issue = "trim"
            values.append(
                {
                    "id": self._clip_key(c),
                    "removed": bool(c.removed),
                    "shot_id": str(c.shot_id) if c.shot_id else None,
                    "source_type": c.source_type,
                    "display_duration_ms": c.display_duration_ms,
                    "transform": c.transform,
                    "media_id": str(c.media_id) if c.media_id else None,
                    "position": c.position,
                    "included": bool(c.included),
                    "muted": bool(c.muted),
                    "trim_in_ms": c.trim_in_ms,
                    "trim_out_ms": c.trim_out_ms,
                    "duration_ms": duration,
                    "script": source["script"] or (m.original_name if m else ""),
                    "shot_position": source["position"],
                    "url": self._derived_url(metadata, "preview_locator") or self._url(m),
                    "poster": self._url(m)
                    if c.source_type == "image"
                    else self._derived_url(metadata, "thumbnail_locator") or source["poster"],
                    "has_audio": bool((metadata or {}).get("has_audio")),
                    "width": ((metadata or {}).get("width") or m.width) if m else None,
                    "height": ((metadata or {}).get("height") or m.height) if m else None,
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
        snapshot = self._snapshot(assembly, active_clips, for_update=for_update)
        context_hash = digest(snapshot)
        library = {}
        for item in values:
            library.setdefault(
                item["shot_id"] or item["media_id"],
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
                select(ProjectRenderJob)
                .where(
                    ProjectRenderJob.assembly_id == assembly.id,
                    ProjectRenderJob.kind.in_(("probe", "export", "preview")),
                )
                .order_by(ProjectRenderJob.created_at.desc(), ProjectRenderJob.id.desc())
                .limit(30)
            )
        )
        # Frequent composite previews must not hide the last playable export.
        latest_export = self.session.scalar(
            select(ProjectRenderJob)
            .where(
                ProjectRenderJob.assembly_id == assembly.id,
                ProjectRenderJob.kind == "export",
                ProjectRenderJob.status == "succeeded",
            )
            .order_by(ProjectRenderJob.created_at.desc(), ProjectRenderJob.id.desc())
            .limit(1)
        )
        if latest_export and not any(j.id == latest_export.id for j in jobs):
            jobs.append(latest_export)
        if assembly.current_media_id and not any(
            j.output_media_id == assembly.current_media_id for j in jobs
        ):
            adopted = self.session.scalar(
                select(ProjectRenderJob).where(
                    ProjectRenderJob.assembly_id == assembly.id,
                    ProjectRenderJob.output_media_id == assembly.current_media_id,
                    ProjectRenderJob.status == "succeeded",
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
                **{
                    key: snapshot[key]
                    for key in ("output_width", "output_height", "output_source", "output_status")
                },
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
            "current_work": self._current_work(assembly, context_hash),
        }

    def _current_work(self, assembly, context_hash):
        media = (
            self.session.get(MediaFile, assembly.current_media_id)
            if assembly.current_media_id
            else None
        )
        if media is None:
            return None
        return AssemblyWorkRead.model_validate(
            {
                "media_id": str(media.id),
                "url": self._url(media),
                "width": media.width,
                "height": media.height,
                "duration_ms": media.duration_ms,
                "is_stale": (media.video_metadata or {}).get("publication_context_hash")
                != context_hash,
            }
        ).model_dump(mode="json")

    def export_locator(self, project_id, job_id):
        with self._transaction(read_only=True):
            _, assembly = self._scope(project_id, for_update=False)
            job = self.session.scalar(
                select(ProjectRenderJob).where(
                    ProjectRenderJob.id == int(job_id),
                    ProjectRenderJob.assembly_id == assembly.id,
                )
            )
            if job is None:
                raise NotFound("导出任务不存在")
            media = (
                self.session.get(MediaFile, job.output_media_id) if job.output_media_id else None
            )
            if job.status != "succeeded" or media is None:
                raise NotFound("成片尚未完成")
            return media.storage_locator

    def current_media(self, project_id):
        with self._transaction(read_only=True):
            _, assembly = self._scope(project_id, for_update=False)
            if not assembly.current_media_id:
                raise NotFound("尚未采用成片")
            return self._require(MediaFile, assembly.current_media_id, for_update=False)

    def get(self, project_id):
        with self._transaction(read_only=True):
            creation, assembly = self._scope(project_id, False, for_update=False)
            return self._read(creation, assembly, for_update=False)

    def jobs(self, project_id, offset=0, limit=20):
        with self._transaction(read_only=True):
            _, assembly = self._scope(project_id, for_update=False)
            context = digest(self._snapshot(assembly, self._clips(assembly), for_update=False))
            jobs = self.session.scalars(
                select(ProjectRenderJob)
                .where(
                    ProjectRenderJob.assembly_id == assembly.id, ProjectRenderJob.kind == "export"
                )
                .order_by(ProjectRenderJob.created_at.desc(), ProjectRenderJob.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
            items = [self._job_read(j, context) for j in jobs]
            return {"items": items[:limit], "has_more": len(items) > limit, "offset": offset}
