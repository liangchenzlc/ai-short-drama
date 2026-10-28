"""标准成片的组合读取、原子保存和素材准入。"""

from copy import deepcopy
from pathlib import Path
from tempfile import SpooledTemporaryFile

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import MediaFile
from short_drama.domain.creation.sound.tracks.sound import ProjectSound
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.creation.assembly.document.assembly import AssemblyVersion
from short_drama.schemas.creation.assembly.editing.editor import (
    AssemblyEditorEdit,
    AssemblyMaterialRead,
)
from short_drama.schemas.creation.sound.tracks.sound import SoundEdit
from short_drama.service.base import utcnow
from short_drama.service.canvas.resources.resource_io import inspect_resource
from short_drama.service.creation.assembly.document.assembly import ProjectAssemblyService
from short_drama.service.creation.sound.tracks.sound import ProjectSoundService
from short_drama.service.creation.writing.script.publication import publish
from short_drama.service.media.files.storage import StorageService
from short_drama.service.media.files.upload_cleanup import cleanup_unreferenced_uploads
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


def voice_context(snapshot):
    """Only voice inputs and picture timing affect a pending recognition result."""
    document = snapshot.get("sound", {}).get("document", {})
    return digest(
        {
            "clips": [
                {k: c.get(k) for k in ("clip_id", "media_id", "trim_in_ms", "trim_out_ms", "muted")}
                for c in snapshot["clips"]
            ],
            "voices": [
                {
                    k: c.get(k)
                    for k in (
                        "kind",
                        "media_id",
                        "start_ms",
                        "duration_ms",
                        "trim_in_ms",
                        "trim_out_ms",
                        "volume",
                        "gain_db",
                        "fade_in_ms",
                        "fade_out_ms",
                        "loop",
                        "loop_offset_ms",
                        "muted",
                    )
                }
                for c in document.get("audio_clips", [])
                if c["kind"] in {"original", "dialogue"}
            ],
            "dialogue": document.get("dialogue", [])
            if document.get("schema_version", 1) == 1
            else [],
            "tracks": [
                {"id": t["id"], "muted": t["muted"]}
                for t in document.get("tracks", [])
                if t["id"] in {"original", "dialogue"}
            ],
            "volumes": [document.get("original_volume", 1), document.get("dialogue_volume", 1)],
        }
    )


class AssemblyEditorService(ProjectAssemblyService):
    def _sync_sound_associations(self, assembly, before, after):
        from short_drama.service.creation.assembly.editing.associations import sync_associations

        sound = self.session.get(ProjectSound, assembly.id, with_for_update=True)
        if sound and sound.document.get("schema_version", 1) >= 2:
            document = sync_associations(sound.document, before, after)
            if sound.document != document:
                sound.document = document
                sound.row_version += 1
                sound.reviewed_timeline_hash = None
                sound.updated_at = utcnow()
                return True
        return False

    def sync(self, project_id, payload):
        data = AssemblyVersion.model_validate(payload)
        with self._transaction():
            creation, assembly = self._scope(project_id)
            self._version(assembly, data.row_version)
            sources = self._sources(creation)
            self._source_version(creation, sources, data.source_hash)
            before = self._video_snapshot(assembly, self._clips(assembly))
            self._sync(assembly, sources)
            after = self._video_snapshot(assembly, self._clips(assembly))
            self._sync_sound_associations(assembly, before, after)
            self._touch(assembly)
            self._probe(assembly)
            return self._editor_read(creation, assembly)

    def _editor_read(self, creation, assembly, *, for_update=True, material_ids=()):
        state = self._read(creation, assembly, for_update=for_update)
        if assembly is None:
            return state
        sound = self.session.get(ProjectSound, assembly.id)
        video = self._video_snapshot(assembly, self._clips(assembly))
        state["sound"] = ProjectSoundService(self.session, self.settings, self.storage)._read(
            creation, assembly, sound, video, for_update=for_update
        )
        from short_drama.service.creation.sound.tracks.render import font_metadata

        state["subtitle_font"] = font_metadata(self.settings)["font"]
        state["voice_context_hash"] = voice_context(self._snapshot(assembly, self._clips(assembly)))
        state["materials"] = self._project_materials(creation.id)
        known = {m["media_id"] for m in state["materials"]}
        missing = {int(mid) for mid in material_ids if mid not in known}
        if missing:
            state["materials"].extend(
                self._material_read(m)
                for m in self.session.scalars(
                    select(MediaFile).where(
                        MediaFile.project_id == creation.id,
                        MediaFile.id.in_(missing),
                        MediaFile.published_at.is_not(None),
                    )
                )
                if m.format_code.startswith(("audio/", "video/", "image/"))
            )
        for material in state["materials"]:
            if material["kind"] == "audio" or material["has_audio"]:
                state["sound"]["media"][material["media_id"]] = {
                    "url": material["url"],
                    "duration_ms": material["duration_ms"] or 0,
                }
        return state

    def get_editor(self, project_id, *, material_ids=()):
        try:
            if len(material_ids) > 900:
                raise ValueError("too many materials")
            material_ids = [str(parse_identifier(mid)) for mid in material_ids]
        except ValueError as exc:
            raise WorkflowError(
                "assembly_material_id", "素材标识须为有效的十进制 ID，单次最多 900 个", 422
            ) from exc
        with self._transaction(read_only=True):
            creation, assembly = self._scope(project_id, required=False, for_update=False)
            return self._editor_read(
                creation, assembly, for_update=False, material_ids=material_ids
            )

    def save_editor(self, project_id, payload):
        data = AssemblyEditorEdit.model_validate(payload)
        with self._transaction():
            creation, assembly = self._scope(project_id)
            fingerprint = digest(data.model_dump(mode="json"))
            receipt = assembly.last_edit_receipt or {}
            if receipt.get("key") == data.request_id:
                if receipt.get("hash") != fingerprint:
                    raise Conflict("保存请求标识已用于其他内容")
                return self._editor_read(creation, assembly)
            self._version(assembly, data.row_version)
            sound = self.session.get(ProjectSound, assembly.id, with_for_update=True)
            if data.sound_row_version != (sound.row_version if sound else 0):
                raise Conflict("声音与字幕已变化，当前输入已保留，请核对版本")
            self._edit_in_transaction(creation, assembly, data)
            video = self._video_snapshot(assembly, self._clips(assembly))
            if sum((c["trim_out_ms"] or 0) - c["trim_in_ms"] for c in video["clips"]) > 3600000:
                raise WorkflowError("assembly_duration_limit", "成片时长不能超过一小时", 422)
            sound_service = ProjectSoundService(self.session, self.settings, self.storage)
            sound_service._save_in_transaction(
                creation,
                assembly,
                sound,
                video,
                SoundEdit(
                    row_version=data.sound_row_version,
                    timeline_hash=digest(video),
                    request_id=data.request_id,
                    document=data.sound,
                    reviewed=True,
                ),
            )
            assembly.last_edit_receipt = {"key": data.request_id, "hash": fingerprint}
            self.session.flush()
            return self._editor_read(creation, assembly)

    def _material_read(self, media):
        metadata = media.video_metadata or {}
        storage = StorageService(self.storage, self.settings)
        return AssemblyMaterialRead.model_validate(
            {
                "media_id": str(media.id),
                "name": media.original_name,
                "kind": media.format_code.split("/")[0],
                "duration_ms": media.duration_ms or metadata.get("duration_ms"),
                "width": media.width or metadata.get("width"),
                "height": media.height or metadata.get("height"),
                "url": storage.download_url(
                    metadata.get("preview_locator") or media.storage_locator
                ),
                "poster": self._derived_url(metadata, "thumbnail_locator"),
                "has_audio": bool(metadata.get("has_audio")),
            }
        ).model_dump(mode="json")

    def materials(self, project_id):
        with self._transaction(read_only=True):
            creation, _ = self._scope(project_id, required=False, for_update=False)
            return {"items": self._project_materials(creation.id)}

    def _project_materials(self, project_id):
        media = self.session.scalars(
            select(MediaFile)
            .where(
                MediaFile.project_id == project_id,
                MediaFile.published_at.is_not(None),
            )
            .order_by(MediaFile.id.desc())
            .limit(300)
        )
        return [
            self._material_read(m)
            for m in media
            if m.format_code.startswith(("audio/", "video/", "image/"))
        ]

    def import_material(self, project_id, mid):
        with self._transaction():
            creation, assembly = self._scope(project_id)
            source = self._require(MediaFile, mid)
            actor = self.session.info.get("actor")
            if source.project_id != creation.id and not (
                source.project_id is None and actor and source.scope_user_id == actor.user_id
            ):
                raise WorkflowError("assembly_material_scope", "只能导入当前项目或本人的素材", 422)
            if not source.format_code.startswith(("audio/", "video/", "image/")):
                raise WorkflowError("assembly_material_type", "仅支持视频、图片和音频", 422)
            if source.project_id == creation.id:
                publish(source)
                if source.format_code.startswith("audio/"):
                    ProjectSoundService(self.session, self.settings, self.storage)._retain(
                        assembly, source.id
                    )
                return self._material_read(source)
            locator, values = (
                source.storage_locator,
                {
                    key: deepcopy(getattr(source, key))
                    for key in (
                        "format_code",
                        "original_name",
                        "byte_size",
                        "width",
                        "height",
                        "duration_ms",
                        "checksum_sha256",
                        "video_metadata",
                    )
                },
            )
        storage = StorageService(self.storage, self.settings)
        with storage.open(locator) as response, SpooledTemporaryFile(max_size=8 << 20) as content:
            size = 0
            for chunk in response.stream(1 << 20):
                size += len(chunk)
                if size > self.settings.render_max_source_bytes:
                    raise WorkflowError("assembly_material_limit", "素材超过处理大小上限", 422)
                content.write(chunk)
            content.seek(0)
            return self.upload_material(
                project_id, content, values["original_name"], values["format_code"]
            )

    def upload_material(self, project_id, stream, filename, mime):
        with self._transaction(read_only=True):
            self._scope(project_id)
        kind = (mime or "").split("/")[0]
        if kind not in {"video", "image", "audio"} or mime == "image/svg+xml":
            raise WorkflowError("assembly_material_type", "请选择视频、位图图片或音频", 422)
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        if not 0 < size <= self.settings.render_max_source_bytes:
            raise WorkflowError("assembly_material_limit", "素材为空或超过处理大小上限", 422)
        inspected = inspect_resource(stream, filename or "material", kind, mime, self.settings)
        if inspected.duration_ms and inspected.duration_ms > 3600000:
            raise WorkflowError("assembly_material_duration", "素材时长不能超过一小时", 422)
        storage = StorageService(self.storage, self.settings)
        stored = storage.upload(stream, length=size, content_type=inspected.mime_type)
        try:
            with self._transaction():
                creation, assembly = self._scope(project_id)
                media = MediaFile(
                    id=next_id(),
                    project_id=creation.id,
                    scope_user_id=None,
                    format_code=inspected.mime_type,
                    storage_locator=stored.storage_locator,
                    original_name=Path(filename or "素材").name[:255],
                    byte_size=size,
                    width=inspected.width,
                    height=inspected.height,
                    duration_ms=inspected.duration_ms,
                    checksum_sha256=inspected.checksum,
                    video_metadata=inspected.video_metadata,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                self.session.add(media)
                publish(media)
                self.session.flush()
                if kind == "audio":
                    ProjectSoundService(self.session, self.settings, self.storage)._retain(
                        assembly, media.id
                    )
                return self._material_read(media)
        except Exception:
            cleanup_unreferenced_uploads(self.session, storage, [stored])
            raise
