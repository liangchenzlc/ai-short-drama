"""读取可信来源项目与分集小说；不写画布、修补正文或调用供应商。"""

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_source_project_dao import CanvasSourceProjectDAO
from short_drama.db.access import require_project
from short_drama.domain import Episode, Project, ProjectCanvas
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.canvas import CanvasKey
from short_drama.schemas.canvas_source_project import (
    CanvasSourceCapabilitiesRead,
    CanvasSourceEpisodeDetailRead,
    CanvasSourceEpisodeRead,
    CanvasSourceNovelMissingRead,
    CanvasSourceNovelPresentRead,
    CanvasSourceProjectRead,
    CanvasSourceProjectSummaryRead,
)

from .canvas_service import CanvasService


class CanvasSourceProjectService(CanvasService):
    def __init__(self, session: Session):
        super().__init__(session)
        self.source = CanvasSourceProjectDAO(session)

    def _bound_canvas(
        self,
        canvas_key: str,
        expected_canvas_row_version: int,
        expected_owning_project_id: int,
        expected_source_project_id: int,
    ) -> tuple[ProjectCanvas, Project]:
        try:
            canvas_key = TypeAdapter(CanvasKey).validate_python(canvas_key)
        except ValidationError:
            raise WorkflowError("invalid_canvas_key", "画布稳定标识格式无效", 422) from None
        canvas = self.canvas_dao.source_canvas(canvas_key)
        if canvas is None:
            raise NotFound("画布不存在或当前账号无权访问")
        identity = (canvas.id, canvas.project_id, canvas.source_project_id)
        if canvas.source_project_id is None:
            raise WorkflowError("canvas_source_project_required", "画布尚未绑定可信业务项目", 409)
        if canvas.source_project_id == canvas.project_id:
            raise WorkflowError("canvas_binding_invalid", "画布项目不能作为自身业务来源", 409)
        projects = {}
        for project_id in sorted({canvas.project_id, canvas.source_project_id}):
            require_project(self.session, project_id, lock=True)
            project = self.canvas_dao.project(project_id, lock=True)
            if project is None:
                raise NotFound("画布所属或来源项目不存在或当前账号无权访问")
            expected_mode = (
                "standard" if project_id == canvas.source_project_id else "infinite_canvas"
            )
            if project.workspace_mode != expected_mode:
                raise WorkflowError("canvas_binding_invalid", "画布绑定的项目模式不匹配", 409)
            projects[project_id] = project
        canvas = self.canvas_dao.source_canvas(canvas_key, lock=True)
        if canvas is None:
            raise NotFound("画布不存在或已归档")
        if (canvas.id, canvas.project_id, canvas.source_project_id) != identity:
            raise WorkflowError("canvas_binding_changed", "画布来源绑定已变化，请重新读取", 409)
        if (canvas.project_id, canvas.source_project_id) != (
            parse_identifier(expected_owning_project_id),
            parse_identifier(expected_source_project_id),
        ):
            raise WorkflowError(
                "canvas_source_context_conflict", "画布所属或来源上下文已变化，请重新读取", 409
            )
        self._check_version(canvas, parse_identifier(expected_canvas_row_version))
        return canvas, projects[canvas.source_project_id]

    @staticmethod
    def _context(canvas: ProjectCanvas) -> dict:
        return {
            "canvas_key": canvas.source_key,
            "canvas_row_version": canvas.row_version,
            "owning_project_id": canvas.project_id,
            "source_project_id": canvas.source_project_id,
        }

    @staticmethod
    def _episode(episode: Episode) -> CanvasSourceEpisodeRead:
        return CanvasSourceEpisodeRead(
            id=episode.id,
            title=episode.title,
            position=episode.position,
            content_version=episode.content_version,
        )

    def overview(
        self,
        canvas_key: str,
        expected_canvas_row_version: int,
        expected_owning_project_id: int,
        expected_source_project_id: int,
    ) -> CanvasSourceProjectRead:
        _ = self.actor_id
        with self._transaction(read_only=True):
            canvas, project = self._bound_canvas(
                canvas_key,
                expected_canvas_row_version,
                expected_owning_project_id,
                expected_source_project_id,
            )
            return CanvasSourceProjectRead(
                **self._context(canvas),
                project=CanvasSourceProjectSummaryRead(
                    id=project.id,
                    row_version=project.row_version,
                    name=project.name,
                    synopsis=project.synopsis,
                    aspect=project.aspect,
                    style_text=project.style,
                ),
                episodes=[self._episode(row) for row in self.source.episodes(project.id)],
                capabilities=CanvasSourceCapabilitiesRead(),
            )

    def episode(
        self,
        canvas_key: str,
        episode_id: int,
        expected_canvas_row_version: int,
        expected_owning_project_id: int,
        expected_source_project_id: int,
        expected_content_version: int,
    ) -> CanvasSourceEpisodeDetailRead:
        _ = self.actor_id
        episode_id = parse_identifier(episode_id)
        expected_content_version = parse_identifier(expected_content_version)
        with self._transaction(read_only=True):
            canvas, project = self._bound_canvas(
                canvas_key,
                expected_canvas_row_version,
                expected_owning_project_id,
                expected_source_project_id,
            )
            episode = self.source.episode(project.id, episode_id)
            if episode is None:
                raise NotFound("分集不存在于该可信来源项目或当前账号无权访问")
            if episode.content_version != expected_content_version:
                raise WorkflowError(
                    "canvas_source_content_conflict", "分集正文版本已变化，请重新读取", 409
                )
            novel = self.source.novel(project.id, episode.id)
            return CanvasSourceEpisodeDetailRead(
                **self._context(canvas),
                episode=self._episode(episode),
                novel=(
                    CanvasSourceNovelPresentRead(plain_text=novel.content)
                    if novel is not None
                    else CanvasSourceNovelMissingRead()
                ),
            )
