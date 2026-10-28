from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    MediaFile,
    ProjectAssembly,
    ProjectAssemblyClip,
    ProjectRenderJob,
)
from short_drama.schemas.creation.assembly.document.assembly import AssemblyVersion
from short_drama.service.base import utcnow
from short_drama.service.creation.assembly.document.base import AssemblyServiceBase
from short_drama.utils.snowflake import next_id


class AssemblyEditing(AssemblyServiceBase):
    def _probe(self, assembly):
        clips = self._clips(assembly, include_removed=True)
        ids = sorted({c.media_id for c in clips if c.media_id})
        pending = [
            m
            for m in self.session.scalars(select(MediaFile).where(MediaFile.id.in_(ids)))
            if m.format_code.startswith("video/")
            and (
                not m.video_metadata
                or m.video_metadata.get("error")
                or not m.video_metadata.get("preview_locator")
                or not m.video_metadata.get("filmstrip_locator")
            )
        ]
        if pending:
            active = self.session.scalars(
                select(ProjectRenderJob).where(
                    ProjectRenderJob.assembly_id == assembly.id,
                    ProjectRenderJob.kind == "probe",
                    ProjectRenderJob.status.in_(["queued", "running"]),
                    ProjectRenderJob.cancel_requested == 0,
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

    def initialize(self, project_id):
        with self._transaction():
            creation, assembly = self._scope(project_id, False)
            sources = self._sources(creation)
            if assembly is None:
                if sum(not s["archived"] for s in sources.values()) > 300:
                    raise WorkflowError("assembly_limit", "成片最多支持300个镜头", 422)
                now = utcnow()
                assembly = ProjectAssembly(
                    id=next_id(),
                    project_id=creation.id,
                    aspect="auto",
                    resolution="720p",
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(assembly)
                self.session.flush()
                self._sync(assembly, sources)
            self._probe(assembly)
            return self._read(creation, assembly, sources)

    def _sync(self, assembly, sources):
        clips = self._clips(assembly, include_removed=True)
        by_shot = {}
        for c in clips:
            by_shot.setdefault(c.shot_id, []).append(c)
        added = [sid for sid, s in sources.items() if not s["archived"] and sid not in by_shot]
        if sum(not c.removed for c in clips) + len(added) > 300:
            raise WorkflowError("assembly_limit", "成片最多支持300个镜头", 422)
        for sid, source in sources.items():
            group = by_shot.get(sid, [])
            if source["archived"]:
                for c in group:
                    c.included = 0
                continue
            if not group:
                c = ProjectAssemblyClip(
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

    def sync(self, project_id, payload):
        data = AssemblyVersion.model_validate(payload)
        with self._transaction():
            creation, assembly = self._scope(project_id)
            self._version(assembly, data.row_version)
            sources = self._sources(creation)
            self._source_version(creation, sources, data.source_hash)
            self._sync(assembly, sources)
            self._touch(assembly)
            self._probe(assembly)
            return self._read(creation, assembly, sources)

    def _edit_in_transaction(self, creation, assembly, data):
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
                if (original is None and not edit.media_id) or "-" not in edit.id:
                    raise WorkflowError("assembly_clip_set", "片段来源不存在或不属于当前项目", 422)
                c = ProjectAssemblyClip(
                    id=next_id(),
                    client_key=edit.id,
                    assembly_id=assembly.id,
                    shot_id=original.shot_id if original else None,
                    media_id=original.media_id if original else int(edit.media_id),
                    source_type=original.source_type if original else edit.source_type,
                    source_context_hash=original.source_context_hash if original else None,
                    position=position,
                    removed=0,
                    included=int(edit.included),
                    muted=int(edit.muted),
                    trim_in_ms=edit.trim_in_ms,
                    trim_out_ms=edit.trim_out_ms,
                )
                self.session.add(c)
                by_id[edit.id] = c
            mid = int(edit.media_id) if edit.media_id else None
            if mid != c.media_id:
                c.shot_id, c.source_context_hash = None, None
                c.source_type = "image" if edit.source_type == "image" else "video"
            c.media_id = mid
            c.display_duration_ms = edit.display_duration_ms
            c.transform = edit.transform.model_dump(mode="json")
            m = self.session.get(MediaFile, c.media_id) if c.media_id else None
            if c.media_id and m is None:
                raise NotFound("画面素材不存在")
            if m:
                if m.format_code.startswith("image/"):
                    if not edit.display_duration_ms:
                        raise WorkflowError("assembly_image_duration", "图片必须设置展示时长", 422)
                    c.source_type, c.shot_id = "image", None
                elif c.source_type == "image":
                    raise WorkflowError("assembly_material_type", "图片片段必须使用图片素材", 422)
            if m and (
                m.project_id != creation.id or not m.format_code.startswith(("video/", "image/"))
            ):
                raise WorkflowError("assembly_media_scope", "请先将素材导入当前项目", 422)
            duration = (
                c.display_duration_ms
                if c.source_type == "image"
                else (m.video_metadata or {}).get("duration_ms")
                if m
                else None
            )
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
        assembly.aspect = data.aspect
        self._touch(assembly)
        self.session.flush()
        return self._read(creation, assembly)
