"""音轨候选采用及上传后的原子写入。"""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    MediaAsset,
    MediaFile,
)
from short_drama.schemas.creation.sound.tracks.sound import SoundAdopt, SoundDocument
from short_drama.service.base import utcnow
from short_drama.service.creation.assembly.rendering.renderer import checksum
from short_drama.service.creation.sound.tracks.base import ProjectSoundServiceBase
from short_drama.service.creation.sound.tracks.snapshot import (
    line_hash as line_hash,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    sound_duration as sound_duration,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    validate_sound_snapshot as validate_sound_snapshot,
)
from short_drama.service.creation.writing.script.publication import publish
from short_drama.service.media.files.storage import StorageService
from short_drama.service.media.files.upload_cleanup import cleanup_unreferenced_uploads
from short_drama.service.media.processing.audio import MAX_AUDIO_BYTES, audio_proxy, inspect_audio
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


class AdoptionOperations(ProjectSoundServiceBase):
    def adopt(self, project_id, payload):
        with self._transaction():
            self.adopt_locked(project_id, payload)
            return self._read(*self._scope(project_id))

    def adopt_locked(self, project_id, payload, *, assembly_version=None, timeline_hash=None):
        """Reuse native adoption inside an existing transaction without serializing media URLs."""
        from short_drama.service.creation.sound.voices.context import project_mode

        data = SoundAdopt.model_validate(payload)
        creation, assembly, sound, video = self._scope(project_id)
        if project_mode(self.session, project_id, self.settings) == "native":
            raise Conflict("原生视频模式不采用独立配音，请在角色库确认音色")
        if sound is None:
            raise Conflict("台词已变化，请刷新")
        if assembly_version is not None and assembly.row_version != assembly_version:
            raise Conflict("画面时间线版本已变化，请刷新")
        if timeline_hash is not None and digest(video) != timeline_hash:
            raise Conflict("画面时间线已变化，请刷新")
        document = deepcopy(sound.document)
        line = next((line for line in document["dialogue"] if line["id"] == data.line_id), None)
        asset = self.session.scalar(select(MediaAsset).where(MediaAsset.media_id == data.media_id))
        record = self.session.get(AIGenerationRecord, asset.record_id) if asset else None
        source = record.request_data.get("source", {}) if record else {}
        if (
            not line
            or source.get("scene") != "dialogue_audio"
            or source.get("project_id") != str(project_id)
            or source.get("line_id") != data.line_id
            or record.request_data["source_snapshot"]["line_hash"] != line_hash(line)
        ):
            raise Conflict("候选与当前台词、模型或音色不一致，请核对后重新生成")
        publish(self._media(data.media_id))
        self.session.flush()
        if line.get("media_id") == str(data.media_id) and line.get("adopted_hash") == line_hash(
            line
        ):
            return {"row_version": str(sound.row_version)}
        if sound.row_version != data.row_version:
            raise Conflict("台词已变化，请刷新")
        self._retain(assembly, data.media_id)
        previous_media = line.get("media_id")
        line.update(media_id=str(data.media_id), adopted_hash=line_hash(line))
        if document.get("schema_version", 1) >= 2:
            from short_drama.service.creation.assembly.editing.associations import picture_spans

            media = self._media(data.media_id)
            anchor = next(
                (
                    span
                    for span in picture_spans(video).values()
                    if span[1] <= line["start_ms"] < span[2]
                ),
                None,
            )
            previous = next(
                (
                    c
                    for c in document.get("audio_clips", [])
                    if c.get("dialogue_id") == line["id"]
                    or (
                        c["kind"] == "dialogue"
                        and c["media_id"] == previous_media
                        and c["start_ms"] == line["start_ms"]
                    )
                ),
                None,
            )
            clip = {
                **(previous or {}),
                "id": previous["id"]
                if previous
                else str(uuid5(NAMESPACE_URL, f"assembly:{assembly.id}:dialogue:{line['id']}")),
                "dialogue_id": line["id"],
                "kind": "dialogue",
                "name": line["character"],
                "media_id": str(media.id),
                "start_ms": line["start_ms"],
                "duration_ms": media.duration_ms,
                "trim_in_ms": 0,
                "trim_out_ms": media.duration_ms,
                "volume": (previous or {}).get("volume", 1),
                "gain_db": (previous or {}).get("gain_db", 0),
                "fade_in_ms": 0,
                "fade_out_ms": 0,
                "loop": False,
                "ducking": False,
                "muted": (previous or {}).get("muted", False),
                "anchor_clip_id": anchor[0]["clip_id"] if anchor else None,
                "anchor_source_in_ms": anchor[0]["trim_in_ms"] + line["start_ms"] - anchor[1]
                if anchor
                else None,
            }
            document["audio_clips"] = [
                c for c in document.get("audio_clips", []) if c["id"] != clip["id"]
            ] + [clip]
            document = SoundDocument.model_validate(document).model_dump(mode="json")
        sound.document, sound.updated_at = document, utcnow()
        sound.row_version += 1
        sound.reviewed_timeline_hash = None
        self.assembly._touch(assembly)
        self.session.flush()
        return {"row_version": str(sound.row_version)}

    def upload(self, project_id, stream, filename):
        # Authorize scope before expensive decoding/storage, then recheck on commit.
        with self._transaction():
            self._scope(project_id)
        storage = StorageService(self.storage, self.settings)
        written = []
        try:
            with TemporaryDirectory() as temp:
                directory = Path(temp)
                path = directory / "original.media"
                size = 0
                with path.open("wb") as output:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_AUDIO_BYTES:
                            raise WorkflowError("audio_limit", "配乐文件不能超过 100 MiB", 422)
                        output.write(chunk)
                try:
                    meta = inspect_audio(path, self.settings)
                    proxy = audio_proxy(path, directory, self.settings)
                except (ValueError, RuntimeError) as error:
                    raise WorkflowError("audio_invalid", str(error), 422) from None
                files = []
                for file, mime, name in (
                    (path, meta["mime"], Path(filename or "music").name[:255]),
                    (proxy, "audio/mp4", "music-preview.m4a"),
                ):
                    with file.open("rb") as content:
                        stored = storage.upload(
                            content, length=file.stat().st_size, content_type=mime
                        )
                    written.append(stored)
                    files.append(
                        MediaFile(
                            id=next_id(),
                            project_id=int(project_id),
                            format_code=mime,
                            storage_locator=stored.storage_locator,
                            original_name=name,
                            duration_ms=meta["duration_ms"],
                            byte_size=file.stat().st_size,
                            checksum_sha256=checksum(file),
                            created_at=utcnow(),
                            updated_at=utcnow(),
                        )
                    )
                with self._transaction():
                    _, assembly, _, _ = self._scope(project_id)
                    self.session.add_all(files)
                    self.session.flush()
                    self._retain(assembly, files[0].id).proxy_media_id = files[1].id
                    self._retain(assembly, files[1].id)
                    result = {
                        "media_id": str(files[0].id),
                        "proxy_media_id": str(files[1].id),
                        "duration_ms": meta["duration_ms"],
                        "url": storage.download_url(files[1].storage_locator),
                    }
                return result
        except Exception:
            cleanup_unreferenced_uploads(self.session, storage, written)
            raise
