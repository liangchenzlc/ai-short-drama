"""Native video speech: project characters, reviewed dialogue and immutable voice references."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AsyncTask,
    Episode,
    EpisodeAsset,
    MediaAsset,
    MediaFile,
    Project,
    ProjectAsset,
    ShotAsset,
    ShotScript,
)
from short_drama.domain.native_voice import CharacterVoice, ProjectSoundMode, ShotDialogue
from short_drama.schemas.native_voice import (
    NativeDialogueEdit,
    NativeVoiceRead,
    SoundModeEdit,
    VoiceAdopt,
)

from .base import BaseService, utcnow
from .episode_assembly_service import digest
from .publication import public_voice_context, publish
from .storage_service import StorageService


def project_mode(session, project_id, settings=None):
    if not getattr(settings or Settings(), "native_video_enabled", False):
        return "legacy"
    row = session.get(ProjectSoundMode, int(project_id))
    return row.mode if row else "legacy"


def native_context(session, shot, *, strict=False, settings=None, _lookups=None):
    if not getattr(settings or Settings(), "native_video_enabled", False):
        return None
    episode = _lookups["episode"] if _lookups is not None else session.get(Episode, shot.episode_id)
    mode = (
        _lookups["mode"]
        if _lookups is not None
        else project_mode(session, episode.project_id, settings)
    )
    if mode != "native":
        return None
    row = (
        _lookups["dialogues"].get(shot.id)
        if _lookups is not None
        else session.get(ShotDialogue, shot.id)
    )
    doc = row.document if row else {"reviewed": False, "lines": []}
    result = {
        "mode": "native",
        "dialogue_version": row.row_version if row else 0,
        "reviewed": doc["reviewed"],
        "lines": deepcopy(doc["lines"]),
        "voices": [],
    }
    if strict and not doc["reviewed"]:
        raise WorkflowError(
            "native_dialogue_review", "请先保存并确认分镜对白；无对白镜头也需确认", 422
        )
    for cid in dict.fromkeys(line["character_id"] for line in doc["lines"]):
        asset = (
            _lookups["assets"].get(int(cid))
            if _lookups is not None
            else session.get(Asset, int(cid))
        )
        binding = (
            _lookups["bindings"].get(int(cid))
            if _lookups is not None
            else session.get(CharacterVoice, (episode.project_id, int(cid)))
        )
        linked = (
            (shot.id, int(cid)) in _lookups["linked"]
            if _lookups is not None
            else session.scalar(
                select(ShotAsset.id).where(
                    ShotAsset.shot_id == shot.id, ShotAsset.asset_id == int(cid)
                )
            )
        )
        if strict and (asset is None or asset.kind != "character" or not linked):
            raise WorkflowError("native_character_scope", "说话角色必须关联到当前分镜", 422)
        media = (
            _lookups["media"].get(binding.media_id)
            if binding and _lookups is not None
            else session.get(MediaFile, binding.media_id)
            if binding
            else None
        )
        if strict and (
            media is None
            or not media.duration_ms
            or not media.checksum_sha256
            or not 3000 <= media.duration_ms <= 7500
            or not media.byte_size
            or media.byte_size > 10 * 1024**2
        ):
            raise WorkflowError(
                "native_voice_required", "角色需采用已归档的 3～7.5 秒样音（最多 10 MiB）", 422
            )
        result["voices"].append(
            {
                "character_id": cid,
                "name": asset.name if asset else "",
                "version": binding.row_version if binding else 0,
                "record_id": str(binding.record_id) if binding else None,
                "media_id": str(media.id) if media else None,
                "checksum": media.checksum_sha256 if media else None,
                "duration_ms": media.duration_ms if media else None,
            }
        )
    return result


def native_contexts(session, episode, shots, *, settings=None):
    """Prefetch this page's native metadata; single-shot validation/serialization is shared."""
    shots = list(shots)
    contexts = {shot.id: None for shot in shots}
    if not shots:
        return contexts
    settings = settings or Settings()
    if not getattr(settings, "native_video_enabled", False):
        return contexts
    mode = project_mode(session, episode.project_id, settings)
    if mode != "native":
        return contexts
    shot_ids = [shot.id for shot in shots]
    dialogues = {
        row.shot_id: row
        for row in session.scalars(select(ShotDialogue).where(ShotDialogue.shot_id.in_(shot_ids)))
    }
    character_ids = {
        int(line["character_id"]) for row in dialogues.values() for line in row.document["lines"]
    }
    assets, bindings, linked, media = {}, {}, set(), {}
    if character_ids:
        assets = {
            row.id: row for row in session.scalars(select(Asset).where(Asset.id.in_(character_ids)))
        }
        bindings = {
            row.asset_id: row
            for row in session.scalars(
                select(CharacterVoice).where(
                    CharacterVoice.project_id == episode.project_id,
                    CharacterVoice.asset_id.in_(character_ids),
                )
            )
        }
        linked = set(
            self_row
            for self_row in session.execute(
                select(ShotAsset.shot_id, ShotAsset.asset_id).where(
                    ShotAsset.shot_id.in_(shot_ids), ShotAsset.asset_id.in_(character_ids)
                )
            ).all()
        )
        media_ids = {row.media_id for row in bindings.values()}
        if media_ids:
            media = {
                row.id: row
                for row in session.scalars(select(MediaFile).where(MediaFile.id.in_(media_ids)))
            }
    lookups = {
        "episode": episode,
        "mode": mode,
        "dialogues": dialogues,
        "assets": assets,
        "bindings": bindings,
        "linked": linked,
        "media": media,
    }
    return {
        shot.id: native_context(session, shot, settings=settings, _lookups=lookups)
        for shot in shots
    }


def native_prompt(context):
    if not context["lines"]:
        return "\n声音要求：本镜头没有对白，不添加人声或背景音乐，可生成必要环境音。"
    parts = [
        "\n声音与对白要求：使用参考音频的固定音色；不要朗读参考样音内容。",
        "只说下列台词，严格按顺序轮流说话，不抢话，不添加台词或背景音乐。",
    ]
    for index, voice in enumerate(context["voices"], 1):
        parts.append(f"角色 {voice['name']}（ID {voice['character_id']}）使用音频{index}的音色。")
    for index, line in enumerate(context["lines"], 1):
        action = (
            "画内说话，口型与对白同步" if line["speech"] == "onscreen" else "画外音，画内人物不张嘴"
        )
        parts.append(
            f"{index}. 角色ID {line['character_id']}，{action}，"
            f"语气：{line['delivery'] or '自然'}；台词：{line['text']}"
        )
    return "\n".join(parts)


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
        linked = self.session.scalar(
            select(ProjectAsset.id).where(
                ProjectAsset.project_id == int(pid), ProjectAsset.asset_id == int(cid)
            )
        )
        if not linked:
            linked = self.session.scalar(
                select(EpisodeAsset.id)
                .join(Episode, Episode.id == EpisodeAsset.episode_id)
                .where(Episode.project_id == int(pid), EpisodeAsset.asset_id == int(cid))
            )
        if asset.kind != "character" or not linked:
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
        from .ai_generation_service import resume_action

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
        from .ai_generation_service import resume_action, safe_error

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
        current_media = self.session.get(MediaFile, binding.media_id) if binding else None
        own_record = self.session.get(AIGenerationRecord, binding.record_id) if binding else None
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
        data = VoiceAdopt.model_validate(payload)
        with self._transaction():
            self._character(pid, cid)
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
            row = self.session.get(CharacterVoice, (int(pid), int(cid)), with_for_update=True)
            if row is None:
                row = CharacterVoice(project_id=int(pid), asset_id=int(cid), row_version=0)
                self.session.add(row)
            row.record_id, row.media_id = int(data.record_id), int(candidate["media_id"])
            publish(self._require(MediaFile, row.media_id))
            row.row_version += 1
            self.session.flush()
            return self._voices(pid, cid)

    def _shot(self, pid, eid, sid, *, for_update=True):
        self._project(pid, for_update=for_update)
        episode = self._require(Episode, eid, for_update=for_update)
        shot = self._require(ShotScript, sid, for_update=for_update)
        if episode.project_id != int(pid) or shot.episode_id != episode.id or shot.deleted_at:
            raise NotFound("分镜不属于当前项目分集")
        return shot

    def _dialogue(self, shot):
        episode = self.session.get(Episode, shot.episode_id)
        row = self.session.get(ShotDialogue, shot.id)
        context = public_voice_context(native_context(self.session, shot, settings=self.settings))
        characters = list(
            self.session.scalars(
                select(Asset)
                .join(ShotAsset, ShotAsset.asset_id == Asset.id)
                .where(ShotAsset.shot_id == shot.id, Asset.kind == "character")
            )
        )
        return {
            "mode": project_mode(self.session, episode.project_id, self.settings),
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

    def dialogue(self, pid, eid, sid):
        with self._transaction(read_only=True):
            return self._dialogue(self._shot(pid, eid, sid, for_update=False))

    def save_dialogue(self, pid, eid, sid, payload):
        data = NativeDialogueEdit.model_validate(payload)
        with self._transaction():
            shot = self._shot(pid, eid, sid)
            row = self.session.get(ShotDialogue, shot.id, with_for_update=True)
            receipt = {"id": data.request_id, "hash": digest(data.model_dump(mode="json"))}
            if row and (row.receipt or {}).get("id") == data.request_id:
                if row.receipt != receipt:
                    raise Conflict("保存标识已用于其他对白")
                return self._dialogue(shot)
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
            if row is None:
                row = ShotDialogue(shot_id=shot.id, row_version=0)
                self.session.add(row)
            row.document, row.receipt = data.document.model_dump(mode="json"), receipt
            row.row_version += 1
            shot.row_version += 1
            shot.updated_at = utcnow()
            from short_drama.dao.episode_storyboard_dao import advance_storyboard_version

            advance_storyboard_version(self.session.get(Episode, shot.episode_id))
            self.session.flush()
            return self._dialogue(shot)
