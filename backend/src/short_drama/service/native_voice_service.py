"""Native video speech: project characters, reviewed dialogue and immutable voice references."""

from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AsyncTask,
    MediaAsset,
    MediaFile,
    Project,
    ShotAsset,
    ShotScript,
)
from short_drama.domain.native_voice import CharacterVoice, ProjectSoundMode, ShotDialogue
from short_drama.schemas.base import UINT64_MAX
from short_drama.schemas.native_voice import (
    NativeDialogueEdit,
    NativeVoiceRead,
    SoundModeEdit,
    VoiceAdopt,
)
from short_drama.utils.json_digest import digest

from .base import BaseService, utcnow
from .native_voice_context import (
    native_context as native_context,
)
from .native_voice_context import (
    native_contexts as native_contexts,
)
from .native_voice_context import (
    native_prompt as native_prompt,
)
from .native_voice_context import (
    project_mode as project_mode,
)
from .publication import public_voice_context, publish
from .storage_service import StorageService


class NativeVoiceService(BaseService):
    model = CharacterVoice

    def __init__(self, session, settings, storage=None):
        super().__init__(session)
        self.settings, self.storage = settings, storage

    def _project(self, pid, *, for_update=True):
        if not self.settings.native_video_enabled or not self.settings.audio_production_enabled:
            raise NotFound("角色声音与原生有声视频功能尚未启用")
        return self._require(Project, pid, for_update=for_update)

    def _character(self, pid, cid, *, for_update=True):
        self._project(pid, for_update=for_update)
        asset = self._require(Asset, cid, for_update=for_update)
        if (
            asset.kind != "character"
            or asset.project_id != int(pid)
            or asset.deleted_at is not None
        ):
            raise NotFound("角色不属于当前项目")
        return asset

    def _mode(self, pid):
        row = self.session.get(ProjectSoundMode, int(pid))
        return {"mode": row.mode if row else "legacy", "row_version": row.row_version if row else 0}

    def mode(self, pid):
        with self._transaction(read_only=True):
            self._project(pid, for_update=False)
            return self._mode(pid)

    def set_mode(self, pid, payload):
        data = SoundModeEdit.model_validate(payload)
        with self._transaction():
            self._project(pid)
            row = self.session.get(ProjectSoundMode, int(pid), with_for_update=True)
            if row and row.mode == data.mode:
                return self._mode(pid)
            if data.row_version != (row.row_version if row else 0):
                raise Conflict("制作模式已变化，请刷新")
            if row is None:
                row = ProjectSoundMode(project_id=int(pid), mode=data.mode, row_version=1)
                self.session.add(row)
            else:
                row.mode, row.row_version = data.mode, row.row_version + 1
            self.session.flush()
            return self._mode(pid)

    def _records(self, pid, cid):
        return self.session.execute(
            select(AIGenerationRecord, AsyncTask)
            .join(AsyncTask, AsyncTask.id == AIGenerationRecord.task_id)
            .where(
                AIGenerationRecord.request_data["source"]["scene"].as_string()
                == "character_voice_design",
                AIGenerationRecord.request_data["source"]["project_id"].as_string() == str(pid),
                AIGenerationRecord.request_data["source"]["asset_id"].as_string() == str(cid),
            )
            .order_by(AIGenerationRecord.id.desc())
            .limit(100)
        ).all()

    def prepare_design(self, payload):
        source = payload["source"]
        character = self._character(source["project_id"], source["asset_id"])
        from .generation_task_policy import resume_action

        if any(
            task.status in {"queued", "running"}
            or record.status in {"sent", "unknown"}
            or resume_action(task, record)
            for record, task in self._records(source["project_id"], character.id)
        ):
            raise WorkflowError(
                "voice_design_active", "该角色已有活动或待恢复音色任务，请先处理原任务", 409
            )
        if not source["voice_prompt"].strip() or not source["preview_text"].strip():
            raise WorkflowError("voice_description_required", "请填写声音描述和试音文本", 422)
        payload["input"] = {
            "voice_prompt": source["voice_prompt"],
            "preview_text": source["preview_text"],
        }
        payload["parameters"] = {}
        payload["source_snapshot"] = {"character_id": str(character.id), "name": character.name}
        return payload

    def _voices(self, pid, cid):
        from .generation_task_policy import resume_action, safe_error

        binding = self.session.get(CharacterVoice, (int(pid), int(cid)))
        candidates = []
        for record, task in self._records(pid, cid):
            media = self.session.scalar(
                select(MediaFile)
                .join(MediaAsset, MediaAsset.media_id == MediaFile.id)
                .where(MediaAsset.record_id == record.id)
            )
            voice = (record.response_data or {}).get("voice") or {}
            candidates.append(
                {
                    "record_id": str(record.id),
                    "task_id": str(task.id),
                    "status": task.status,
                    "can_resume": bool(resume_action(task, record)),
                    "error": safe_error(task.error),
                    "voice_id": voice.get("voice_id"),
                    "description": record.request_data["input"]["voice_prompt"],
                    "preview_text": record.request_data["input"]["preview_text"],
                    "media_id": str(media.id) if media else None,
                    "duration_ms": media.duration_ms if media else None,
                    "url": StorageService(self.storage, self.settings).download_url(
                        media.storage_locator
                    )
                    if media and self.storage
                    else None,
                    "adoptable": bool(
                        task.status == "succeeded"
                        and media
                        and media.checksum_sha256
                        and media.byte_size
                        and media.byte_size <= 10 * 1024**2
                        and voice.get("voice_id")
                        and media.duration_ms
                        and 3000 <= media.duration_ms <= 7500
                    ),
                }
            )
        current_media = (
            self.session.get(MediaFile, binding.media_id) if binding and binding.media_id else None
        )
        own_record = (
            self.session.get(AIGenerationRecord, binding.record_id)
            if binding and binding.record_id
            else None
        )
        return NativeVoiceRead.model_validate(
            {
                "row_version": binding.row_version if binding else 0,
                "record_id": str(binding.record_id) if own_record else None,
                "current_voice": {
                    "media_id": str(current_media.id),
                    "url": StorageService(self.storage, self.settings).download_url(
                        current_media.storage_locator
                    )
                    if self.storage
                    else None,
                    "duration_ms": current_media.duration_ms,
                    "row_version": binding.row_version,
                }
                if current_media
                else None,
                "candidates": candidates,
            }
        ).model_dump(mode="json")

    def voices(self, pid, cid):
        with self._transaction(read_only=True):
            self._character(pid, cid, for_update=False)
            return self._voices(pid, cid)

    def adopt(self, pid, cid, payload):
        with self._transaction():
            return self.adopt_locked(pid, cid, payload)

    def adopt_locked(self, pid, cid, payload):
        if not self.session.in_transaction():
            raise BusinessError("Voice adoption requires the caller's transaction")
        data = VoiceAdopt.model_validate(payload)
        self._character(pid, cid)
        row = self.session.get(CharacterVoice, (int(pid), int(cid)), with_for_update=True)
        current = self._voices(pid, cid)
        candidate = next(
            (c for c in current["candidates"] if c["record_id"] == str(data.record_id)), None
        )
        if not candidate or not candidate["adoptable"]:
            raise Conflict("请采用当前角色已归档的 3～7.5 秒声音候选")
        if current["record_id"] == str(data.record_id):
            return current
        if current["row_version"] != data.row_version:
            raise Conflict("角色音色已变化，请刷新后采用")
        if current["row_version"] == UINT64_MAX:
            raise Conflict("角色音色版本已达到上限，无法采用新音色")
        if row is None:
            row = CharacterVoice(project_id=int(pid), asset_id=int(cid), row_version=0)
            self.session.add(row)
        row.record_id, row.media_id = int(data.record_id), int(candidate["media_id"])
        publish(self._require(MediaFile, row.media_id))
        row.row_version += 1
        self.session.flush()
        return self._voices(pid, cid)

    def _shot(self, pid, sid, *, for_update=True):
        creation = self._project(pid, for_update=for_update)
        shot = self._require(ShotScript, sid, for_update=for_update)
        if creation.id != int(pid) or shot.project_id != creation.id or shot.deleted_at:
            raise NotFound("分镜不属于当前项目分集")
        return shot

    def _dialogue(self, shot):
        creation = self.session.get(Project, shot.project_id)
        row = self.session.get(ShotDialogue, shot.id)
        context = public_voice_context(native_context(self.session, shot, settings=self.settings))
        characters = list(
            self.session.scalars(
                select(Asset)
                .join(ShotAsset, ShotAsset.asset_id == Asset.id)
                .where(
                    ShotAsset.shot_id == shot.id,
                    Asset.kind == "character",
                    Asset.deleted_at.is_(None),
                )
            )
        )
        return {
            "mode": project_mode(self.session, creation.id, self.settings),
            "row_version": row.row_version if row else 0,
            "document": row.document if row else {"lines": [], "reviewed": False},
            "characters": [{"id": str(a.id), "name": a.name} for a in characters],
            "voices": [{**v, "url": self._voice_url(v)} for v in (context or {}).get("voices", [])],
        }

    def _voice_url(self, voice):
        media = (
            self.session.get(MediaFile, int(voice["media_id"])) if voice.get("media_id") else None
        )
        return (
            StorageService(self.storage, self.settings).download_url(media.storage_locator)
            if media and self.storage
            else None
        )

    def dialogue(self, pid, sid):
        with self._transaction(read_only=True):
            return self._dialogue(self._shot(pid, sid, for_update=False))

    def save_dialogue(self, pid, sid, payload):
        data = NativeDialogueEdit.model_validate(payload)
        with self._transaction():
            row = self.save_dialogue_locked(pid, sid, data)
            return self._dialogue(self.session.get(ShotScript, row.shot_id))

    def save_dialogue_locked(self, pid, sid, payload, *, skip_unchanged=False):
        """调用者持有事务；保留功能准入、本人范围、角色关联及原生对白版本规则。"""
        if not self.session.in_transaction():
            raise BusinessError("Dialogue save requires the caller's transaction")
        data = NativeDialogueEdit.model_validate(payload)
        shot = self._shot(pid, sid)
        row = self.session.get(ShotDialogue, shot.id, with_for_update=True)
        receipt = {"id": data.request_id, "hash": digest(data.model_dump(mode="json"))}
        if row and (row.receipt or {}).get("id") == data.request_id:
            if row.receipt != receipt:
                raise Conflict("保存标识已用于其他对白")
            return row
        if data.row_version != (row.row_version if row else 0):
            raise Conflict("对白已变化，请刷新")
        for line in data.document.lines:
            self._character(pid, line.character_id)
            if not self.session.scalar(
                select(ShotAsset.id).where(
                    ShotAsset.shot_id == shot.id, ShotAsset.asset_id == int(line.character_id)
                )
            ):
                raise Conflict("说话角色必须关联到当前分镜")
        document = data.document.model_dump(mode="json")
        if row is not None and skip_unchanged and row.document == document:
            return row
        if row is not None and row.row_version >= UINT64_MAX:
            raise Conflict("Dialogue version exhausted")
        if row is None:
            row = ShotDialogue(shot_id=shot.id, row_version=0)
            self.session.add(row)
        row.document, row.receipt = document, receipt
        row.row_version += 1
        from short_drama.dao.project_storyboard_dao import (
            advance_shot_version,
            advance_storyboard_version,
        )

        advance_shot_version(shot)
        shot.updated_at = utcnow()
        advance_storyboard_version(self.session.get(Project, shot.project_id))
        self.session.flush()
        return row
