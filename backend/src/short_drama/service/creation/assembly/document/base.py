from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    ProjectAssembly,
    ProjectAssemblyClip,
    ShotScript,
)
from short_drama.service.base import BaseService, utcnow
from short_drama.service.creation.storyboard.management.storyboard import (
    ProjectStoryboardService,
)
from short_drama.service.creation.storyboard.videos.video_context import (
    DEFAULT_VIDEO_SETTINGS,
    video_context_hash,
)
from short_drama.utils.json_digest import digest as digest


class AssemblyServiceBase(BaseService):
    model = ProjectAssembly

    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.settings, self.storage = settings, storage
        self.storyboard = ProjectStoryboardService(session, settings, storage)

    def _scope(self, project_id, required=True, *, for_update=True):
        creation = self.storyboard.lock_project(project_id, for_update=for_update)
        statement = select(ProjectAssembly).where(ProjectAssembly.project_id == creation.id)
        if for_update:
            statement = statement.with_for_update()
        assembly = self.session.scalar(statement)
        if required and assembly is None:
            raise NotFound("尚未创建成片草稿")
        return creation, assembly

    @staticmethod
    def _clip_key(clip):
        return clip.client_key or str(clip.id)

    def _clips(self, assembly, include_removed=False):
        return list(
            self.session.scalars(
                select(ProjectAssemblyClip)
                .where(ProjectAssemblyClip.assembly_id == assembly.id)
                .where(True if include_removed else ProjectAssemblyClip.removed == 0)
                .order_by(ProjectAssemblyClip.position)
            )
        )

    def _sources(self, creation):
        shots = list(
            self.session.scalars(
                select(ShotScript)
                .where(ShotScript.project_id == creation.id)
                .order_by(ShotScript.position, ShotScript.id)
            )
        )
        ids = [s.id for s in shots]
        assets, images = self.storyboard.dao.list_details(ids)
        videos = self.storyboard.dao.video_rows(ids)
        from short_drama.service.creation.sound.voices.context import native_contexts

        native = native_contexts(self.session, creation, shots, settings=self.settings)
        result = {}
        for shot in shots:
            _, context = self.storyboard._context(creation, shot, assets[shot.id])
            image = images.get(shot.id)
            current_hash = video_context_hash(
                context,
                str(image[1].id) if image else None,
                shot.video_prompt or "",
                shot.video_settings or DEFAULT_VIDEO_SETTINGS,
                session=self.session,
                shot=shot,
                native_speech=native[shot.id],
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
    def _source_hash(sources, creation):
        return digest(
            {
                "sources": [
                    {k: v for k, v in s.items() if k != "poster"} for s in sources.values()
                ],
            }
        )

    @staticmethod
    def _version(assembly, version):
        if assembly.row_version != int(version):
            raise WorkflowError(
                "assembly_version_conflict", "成片草稿已在其他窗口修改，请保留草稿后重新载入", 409
            )

    def _source_version(self, creation, sources, expected):
        if self._source_hash(sources, creation) != expected:
            raise WorkflowError("assembly_source_changed", "分镜来源已变化，请刷新核对后重试", 409)

    @staticmethod
    def _touch(assembly):
        assembly.row_version += 1
        assembly.updated_at = utcnow()
