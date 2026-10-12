"""可信画布来源的窄只读合同；不仿造源项目聚合详情。"""

from typing import Annotated, Literal

from pydantic import Field

from .base import Identifier, PositiveUInt32, ReadModel
from .canvas import CanvasKey


class CanvasSourceContextRead(ReadModel):
    canvas_key: CanvasKey
    canvas_row_version: Identifier
    owning_project_id: Identifier
    source_project_id: Identifier


class CanvasSourceProjectSummaryRead(ReadModel):
    id: Identifier
    row_version: Identifier
    name: str
    synopsis: str
    aspect: Literal["16:9", "9:16"]
    style_text: str


class CanvasSourceEpisodeRead(ReadModel):
    id: Identifier
    title: str
    position: PositiveUInt32
    content_version: Identifier
    kind: Literal["episode"] = "episode"


class CanvasSourceCapabilitiesRead(ReadModel):
    source_style_snapshot: Literal["unavailable"] = "unavailable"
    source_asset_folders: Literal["unavailable"] = "unavailable"
    source_assets: Literal["not_included"] = "not_included"


class CanvasSourceProjectRead(CanvasSourceContextRead):
    project: CanvasSourceProjectSummaryRead
    episodes: list[CanvasSourceEpisodeRead]
    capabilities: CanvasSourceCapabilitiesRead


class CanvasSourceNovelPresentRead(ReadModel):
    state: Literal["present"] = "present"
    content_format: Literal["plain_text"] = "plain_text"
    plain_text: str


class CanvasSourceNovelMissingRead(ReadModel):
    state: Literal["missing"] = "missing"
    content_format: Literal["plain_text"] = "plain_text"
    plain_text: None = None


class CanvasSourceEpisodeDetailRead(CanvasSourceContextRead):
    episode: CanvasSourceEpisodeRead
    novel: Annotated[
        CanvasSourceNovelPresentRead | CanvasSourceNovelMissingRead, Field(discriminator="state")
    ]
