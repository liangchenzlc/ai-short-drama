"""来源项目专用只读路由；expected参数只校验上下文，不授予权限。"""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas import CanvasKey
from short_drama.schemas.canvas_source_project import (
    CanvasSourceEpisodeDetailRead,
    CanvasSourceProjectRead,
)
from short_drama.service.canvas_source_project_service import CanvasSourceProjectService

router = APIRouter(tags=["Canvas source project"])
PATH = "/canvas-runtime/canvas-projects/{canvas_key}/source-project"
CanvasSourceKey = Annotated[CanvasKey, Path()]
EpisodeId = Annotated[Identifier, Path()]
ExpectedIdentifier = Annotated[Identifier, Query()]


@router.get(PATH, response_model=CanvasSourceProjectRead)
def get_canvas_source_project(
    canvas_key: CanvasSourceKey,
    service: Canvases,
    expected_canvas_row_version: ExpectedIdentifier,
    expected_owning_project_id: ExpectedIdentifier,
    expected_source_project_id: ExpectedIdentifier,
):
    return CanvasSourceProjectService(service.session).overview(
        canvas_key,
        expected_canvas_row_version,
        expected_owning_project_id,
        expected_source_project_id,
    )


@router.get(PATH + "/episodes/{episode_id}", response_model=CanvasSourceEpisodeDetailRead)
def get_canvas_source_episode(
    canvas_key: CanvasSourceKey,
    episode_id: EpisodeId,
    service: Canvases,
    expected_canvas_row_version: ExpectedIdentifier,
    expected_owning_project_id: ExpectedIdentifier,
    expected_source_project_id: ExpectedIdentifier,
    expected_content_version: ExpectedIdentifier,
):
    return CanvasSourceProjectService(service.session).episode(
        canvas_key,
        episode_id,
        expected_canvas_row_version,
        expected_owning_project_id,
        expected_source_project_id,
        expected_content_version,
    )
