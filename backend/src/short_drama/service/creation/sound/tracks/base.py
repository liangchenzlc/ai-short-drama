"""声音制作的作品权限、媒体引用与共享依赖。"""

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    MediaFile,
)
from short_drama.domain.creation.sound.tracks.sound import (
    ProjectSound,
    SoundMediaReference,
)
from short_drama.service.base import BaseService
from short_drama.service.creation.assembly.document.assembly import ProjectAssemblyService
from short_drama.service.creation.sound.tracks.snapshot import (
    line_hash as line_hash,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    sound_duration as sound_duration,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    sound_media_rows,
)
from short_drama.service.creation.sound.tracks.snapshot import (
    validate_sound_snapshot as validate_sound_snapshot,
)


class ProjectSoundServiceBase(BaseService):
    model = ProjectSound

    def __init__(self, session, settings, storage=None):
        super().__init__(session)
        self.settings, self.storage = settings, storage
        self.assembly = ProjectAssemblyService(session, settings, storage)

    def _scope(self, project_id, *, for_update=True):
        if not getattr(self.settings, "audio_production_enabled", False):
            raise NotFound("声音制作功能尚未启用")
        creation, assembly = self.assembly._scope(project_id, for_update=for_update)
        sound = self.session.get(ProjectSound, assembly.id, with_for_update=for_update)
        video = self.assembly._video_snapshot(assembly, self.assembly._clips(assembly))
        return creation, assembly, sound, video

    def _media(self, mid, *, for_update=True):
        media = self._validate_media(int(mid), "audio", for_update=for_update)
        if not media.duration_ms or not media.storage_locator.startswith("minio://"):
            raise WorkflowError("audio_unavailable", "音频尚未验证或无法读取", 422)
        return media

    def _retain(self, assembly, mid):
        row = self.session.get(SoundMediaReference, (assembly.id, int(mid)))
        if row is None:
            row = SoundMediaReference(assembly_id=assembly.id, media_id=int(mid))
            self.session.add(row)
        return row

    def _editor_media(self, mid, *, for_update=True):
        media = self._require(MediaFile, mid, for_update=for_update)
        if not media.format_code.startswith(
            ("audio/", "video/")
        ) or not media.storage_locator.startswith("minio://"):
            raise WorkflowError("audio_unavailable", "声音来源不是可读取的音视频", 422)
        return media

    def _media_rows(self, identifiers, *, for_update=True, audio_only=False):
        return sound_media_rows(
            self.session, identifiers, for_update=for_update, audio_only=audio_only
        )
