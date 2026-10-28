"""声音文档版本保存及不可变导出快照。"""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import (
    AIModelConfig,
)
from short_drama.domain.creation.sound.tracks.sound import (
    ProjectSound,
    SoundMediaReference,
)
from short_drama.schemas.creation.sound.tracks.sound import SoundEdit
from short_drama.service.base import utcnow
from short_drama.service.creation.sound.tracks.base import ProjectSoundServiceBase
from short_drama.service.creation.sound.tracks.snapshot import (
    line_hash as line_hash,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    sound_duration as sound_duration,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    sound_snapshot,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    validate_sound_snapshot as validate_sound_snapshot,
)
from short_drama.service.creation.writing.script.publication import publish
from short_drama.utils.json_digest import digest


class EditingOperations(ProjectSoundServiceBase):
    def save(self, project_id, payload):
        data = SoundEdit.model_validate(payload)
        with self._transaction():
            creation, assembly, sound, video = self._scope(project_id)
            if (
                sound
                and sound.document.get("schema_version", 1) >= 2
                and data.document.schema_version < 2
            ):
                raise Conflict("此声音草稿已使用多轨编辑，请刷新页面")
            return self._save_in_transaction(creation, assembly, sound, video, data)

    def _save_in_transaction(
        self, creation, assembly, sound, video, data, *, read_result=True, restored_adoptions=None
    ):
        receipt = {"request_id": data.request_id, "hash": digest(data.model_dump(mode="json"))}
        if sound and (sound.last_receipt or {}).get("request_id") == data.request_id:
            if sound.last_receipt != receipt:
                raise Conflict("声音保存请求标识已用于其他内容")
            return (
                self._read(creation, assembly, sound, video)
                if read_result
                else {"row_version": str(sound.row_version)}
            )
        if data.row_version != (sound.row_version if sound else 0) or data.timeline_hash != digest(
            video
        ):
            raise Conflict("声音草稿或视频剪辑已变化，请刷新核对后保存")
        document = data.document.model_dump(mode="json")
        from short_drama.service.creation.sound.voices.context import project_mode

        native = project_mode(self.session, creation.id, self.settings) == "native"
        if native and document["dialogue"]:
            raise WorkflowError(
                "native_no_dubbing", "原生视频模式不叠加独立配音，请在分镜中配置对白", 422
            )
        old = {line["id"]: line for line in (sound.document["dialogue"] if sound else [])}
        for line in document["dialogue"]:
            previous = old.get(line["id"], {})
            if self.session.info.get("actor"):
                if line["config_id"]:
                    self._validate_model(int(line["config_id"]), "audio")
                elif (
                    previous.get("config_id")
                    and self.session.scalar(
                        select(AIModelConfig.id).where(
                            AIModelConfig.id == int(previous["config_id"])
                        )
                    )
                    is None
                ):
                    # A shared page omits the private model ID. Preserve it
                    # internally when saving unrelated sound or timing edits.
                    line["config_id"] = previous["config_id"]
            adoption = (line["media_id"], line["adopted_hash"])
            if (
                line["media_id"]
                and adoption != (previous.get("media_id"), previous.get("adopted_hash"))
                and adoption != (restored_adoptions or {}).get(line["id"])
            ):
                raise Conflict("请从当前台词的配音候选中采用音频")
            if not line["media_id"]:
                line["adopted_hash"] = None
            if line["media_id"]:
                self._media(line["media_id"])
                self._retain(assembly, line["media_id"])
        if document["music"]:
            mid = document["music"]["media_id"]
            if self.session.get(SoundMediaReference, (assembly.id, int(mid))) is None:
                raise Conflict("配乐必须先上传到当前集")
            m = self._media(mid)
            if document["music"]["trim_out_ms"] > m.duration_ms:
                raise WorkflowError("audio_timing", "配乐裁剪超出音频实际时长", 422)
            publish(m)
            reference = self.session.get(SoundMediaReference, (assembly.id, int(mid)))
            if reference.proxy_media_id:
                publish(self._media(reference.proxy_media_id))
        for clip in document["audio_clips"]:
            media = self._editor_media(clip["media_id"])
            duration = media.duration_ms or (media.video_metadata or {}).get("duration_ms")
            if (
                media.project_id != creation.id
                and self.session.get(SoundMediaReference, (assembly.id, media.id)) is None
            ):
                raise Conflict("请先将声音素材导入当前集")
            if not duration or clip["trim_out_ms"] > duration:
                raise WorkflowError("audio_timing", "声音裁剪超出来源实际时长", 422)
            self._retain(assembly, media.id)
            publish(media)
        if sound is None:
            sound = ProjectSound(assembly_id=assembly.id, row_version=0, document={})
            self.session.add(sound)
        # Preserve legacy dialogue behind mode switches; native exports always filter it.
        if native and sound.document:
            document["dialogue"] = deepcopy(sound.document.get("dialogue", []))
        sound.document = document
        sound.row_version += 1
        sound.last_receipt = receipt
        sound.updated_at = utcnow()
        sound.reviewed_timeline_hash = digest(video) if data.reviewed else None
        self.assembly._touch(assembly)
        self.session.flush()
        return (
            self._read(creation, assembly, sound, video)
            if read_result
            else {"row_version": str(sound.row_version)}
        )

    def snapshot(self, assembly, video, *, for_update=True):
        return sound_snapshot(self.session, self.settings, assembly, video, for_update=for_update)
