"""声音编辑文档、对白候选与音色读取。"""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    MediaAsset,
    MediaFile,
)
from short_drama.domain.creation.sound.tracks.sound import (
    ProjectVoiceDefaults,
    SoundMediaReference,
)
from short_drama.schemas.creation.sound.tracks.sound import SoundDocument
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
from short_drama.service.media.files.storage import StorageService
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


class ReadingOperations(ProjectSoundServiceBase):
    def _read(self, creation, assembly, sound, video, *, for_update=True):
        document = deepcopy(sound.document) if sound else SoundDocument().model_dump(mode="json")
        from short_drama.service.creation.sound.voices.context import project_mode

        mode = project_mode(self.session, creation.id, self.settings)
        if mode == "native":
            document["dialogue"] = []
        document.setdefault("native_ducking", [])
        document = SoundDocument.model_validate(document).model_dump(mode="json")
        voices = self.session.get(ProjectVoiceDefaults, creation.id)
        stale_lines = [
            line["id"]
            for line in document["dialogue"]
            if line.get("media_id") and line.get("adopted_hash") != line_hash(line)
        ]
        actor = self.session.info.get("actor")
        if actor:
            config_ids = {
                int(line["config_id"]) for line in document["dialogue"] if line.get("config_id")
            }
            visible_configs = set(
                self.session.scalars(
                    select(AIModelConfig.id).where(AIModelConfig.id.in_(config_ids))
                )
            )
            for line in document["dialogue"]:
                if line.get("config_id") and int(line["config_id"]) not in visible_configs:
                    line["config_id"] = None
        media_ids = (
            {line["media_id"] for line in document["dialogue"] if line.get("media_id")}
            | ({document["music"]["media_id"]} if document["music"] else set())
            | {clip["media_id"] for clip in document["audio_clips"]}
        )
        rows = self._media_rows(media_ids, for_update=for_update)
        references = (
            {
                ref.media_id: ref
                for ref in self.session.scalars(
                    select(SoundMediaReference).where(
                        SoundMediaReference.assembly_id == assembly.id,
                        SoundMediaReference.media_id.in_(rows),
                    )
                )
            }
            if rows
            else {}
        )
        uploads = self.session.execute(
            select(SoundMediaReference, MediaFile)
            .join(MediaFile, MediaFile.id == SoundMediaReference.media_id)
            .where(
                SoundMediaReference.assembly_id == assembly.id,
                SoundMediaReference.proxy_media_id.is_not(None),
                *([MediaFile.created_by == actor.user_id] if actor else []),
            )
            .order_by(MediaFile.id.desc())
            .limit(100)
        ).all()
        proxies = self._media_rows(
            {ref.proxy_media_id for ref in references.values() if ref.proxy_media_id}
            | {ref.proxy_media_id for ref, _ in uploads},
            for_update=for_update,
            audio_only=True,
        )
        media = {}
        storage = StorageService(self.storage, self.settings)
        for mid, row in rows.items():
            reference = references.get(mid)
            preview = (
                proxies[reference.proxy_media_id] if reference and reference.proxy_media_id else row
            )
            media[str(mid)] = {
                "duration_ms": row.duration_ms or (row.video_metadata or {}).get("duration_ms"),
                "url": storage.download_url(preview.storage_locator),
            }
        return {
            "mode": mode,
            "row_version": str(sound.row_version if sound else 0),
            "timeline_hash": digest(video),
            "duration_ms": sound_duration(video),
            "needs_review": bool(sound and sound.reviewed_timeline_hash != digest(video)),
            "document": document,
            "media": media,
            "uploads": [
                {
                    "media_id": str(row.id),
                    "name": row.original_name,
                    "duration_ms": row.duration_ms,
                    "url": storage.download_url(proxies[ref.proxy_media_id].storage_locator),
                }
                for ref, row in uploads
            ],
            "stale_lines": stale_lines,
            "voice_defaults": {
                "row_version": str(voices.row_version if voices else 0),
                "voices": voices.voices if voices else {},
            },
        }

    def get(self, project_id):
        with self._transaction(read_only=True):
            return self._read(*self._scope(project_id, for_update=False), for_update=False)

    def native_subtitles(self, project_id):
        with self._transaction(read_only=True):
            creation, _, _, video = self._scope(project_id, for_update=False)
            from short_drama.service.creation.sound.voices.context import project_mode

            if project_mode(self.session, creation.id, self.settings) != "native":
                raise WorkflowError("native_mode_required", "请先切换为原生有声视频模式", 422)
            subtitles, offset_frames = [], 0
            for clip in video["clips"]:
                if clip["trim_out_ms"] is None:
                    raise WorkflowError("video_unavailable", "请先完成视频探测", 422)
                frames = (clip["trim_out_ms"] * 30 + 500) // 1000 - (
                    clip["trim_in_ms"] * 30 + 500
                ) // 1000
                media = (
                    self.session.get(MediaFile, int(clip["media_id"])) if clip["media_id"] else None
                )
                native = (
                    (media.video_metadata or {}).get("adopted_native_speech") if media else None
                )
                lines = (native or {}).get("lines", []) if not clip["muted"] else []
                for i, line in enumerate(lines):
                    start = round((offset_frames + frames * i / len(lines)) * 1000 / 30)
                    end = round((offset_frames + frames * (i + 1) / len(lines)) * 1000 / 30)
                    if end > start:
                        subtitles.append({"start_ms": start, "end_ms": end, "text": line["text"]})
                offset_frames += frames
            return {
                "subtitles": subtitles,
                "timeline_hash": digest(video),
                "reviewed": False,
                "notice": "仅按采用视频的冻结台词生成均分时间草稿，"
                "裁剪可能截断台词；请逐句试听校对。",
            }

    def voices(self, project_id, data):
        with self._transaction():
            creation, _, _, _ = self._scope(project_id)
            row = self.session.get(ProjectVoiceDefaults, creation.id, with_for_update=True)
            if data.row_version != (row.row_version if row else 0):
                raise Conflict("角色音色设置已变化，请刷新")
            if not row:
                row = ProjectVoiceDefaults(project_id=creation.id, row_version=0, voices={})
                self.session.add(row)
            row.row_version += 1
            row.voices = data.voices
            return {"row_version": str(row.row_version), "voices": row.voices}

    def extraction(self, project_id, task_id):
        import json

        from short_drama.schemas.creation.sound.tracks.sound import Dialogue

        with self._transaction():
            self._scope(project_id)
            record = self.session.scalar(
                select(AIGenerationRecord)
                .where(AIGenerationRecord.task_id == task_id)
                .order_by(AIGenerationRecord.call_no.desc())
            )
            source = record.request_data.get("source", {}) if record else {}
            if source.get("scene") != "dialogue_extract" or source.get("project_id") != str(
                project_id
            ):
                raise NotFound("台词提取任务不存在")
            task = self.session.get(AsyncTask, task_id)
            result = {
                "status": task.status,
                "raw_text": record.text_content or "",
                "dialogue": None,
            }
            if task.status == "succeeded":
                try:
                    text = record.text_content.strip()
                    if text.startswith("```json") and text.endswith("```"):
                        text = text[7:-3].strip()
                    items = json.loads(text)
                    if not isinstance(items, list) or len(items) > 300:
                        raise ValueError()
                    if any(
                        not isinstance(item, dict) or set(item) - {"character", "text", "start_ms"}
                        for item in items
                    ):
                        raise ValueError()
                    result["dialogue"] = [
                        Dialogue(id=f"line_{next_id()}", **item).model_dump(mode="json")
                        for item in items
                    ]
                except (ValueError, TypeError):
                    result["error"] = "返回内容未通过台词格式校验，原文已保留，可人工整理。"
            return result

    def candidates(self, project_id, line_id):
        with self._transaction():
            self._scope(project_id)
            rows = self.session.execute(
                select(AIGenerationRecord, AsyncTask)
                .join(AsyncTask, AsyncTask.id == AIGenerationRecord.task_id)
                .where(
                    AIGenerationRecord.request_data["source"]["scene"].as_string()
                    == "dialogue_audio",
                    AIGenerationRecord.request_data["source"]["project_id"].as_string()
                    == str(project_id),
                    AIGenerationRecord.request_data["source"]["line_id"].as_string() == line_id,
                )
                .order_by(AIGenerationRecord.id.desc())
                .limit(100)
            ).all()
            result = []
            for record, task in rows:
                assets = self.session.scalars(
                    select(MediaAsset).where(MediaAsset.record_id == record.id)
                ).all()
                from short_drama.service.generations.admission.task_policy import (
                    resume_action,
                    safe_error,
                )

                result.append(
                    {
                        "id": str(task.id),
                        "status": task.status,
                        "can_resume": bool(resume_action(task, record)),
                        "error": safe_error(task.error),
                        "line_hash": record.request_data["source_snapshot"]["line_hash"],
                        "outputs": [
                            {
                                "media_id": str(a.media_id),
                                "url": StorageService(self.storage, self.settings).download_url(
                                    self._media(a.media_id).storage_locator
                                ),
                                "duration_ms": self._media(a.media_id).duration_ms,
                            }
                            for a in assets
                        ],
                    }
                )
            return result
