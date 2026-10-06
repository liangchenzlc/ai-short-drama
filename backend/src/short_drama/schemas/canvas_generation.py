"""原版任务绑定协议；结果只从服务器读取，不接受客户端产物。"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel

from .base import Identifier, InputModel
from .canvas import CanvasDocument, CanvasKey, SourceKey


class CanvasTaskRegistration(InputModel):
    project_id: Identifier
    canvas_id: Identifier
    task_id: Identifier
    node_key: SourceKey
    source_node_key: SourceKey | None = None
    client_operation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9:_-]{8,128}$")]


class CanvasGenerationProtocol(InputModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)


class CanvasTaskBindParams(CanvasGenerationProtocol):
    canvas_id: CanvasKey
    task_id: Identifier
    node_id: SourceKey
    output_index: int = Field(default=0, strict=True, ge=0, le=2**32 - 1)


class CanvasTaskBindOperation(CanvasGenerationProtocol):
    op_id: str = Field(min_length=1, max_length=256, pattern=r"^[\w:.-]+$")
    params: CanvasTaskBindParams


class CanvasTaskBindReceipt(CanvasGenerationProtocol):
    applied: bool
    canvas_id: str
    node_id: str
    task_id: Identifier
    output_index: int
    effect_key: str
    media_type: Literal["text", "image", "video", "audio"]
    already_bound: bool
    historical: dict[str, JsonValue]
    binding_status: Literal["bound", "deleted", "replaced"]
    revision: Identifier | None = None
    canvas: CanvasDocument | None = None
    node: dict[str, JsonValue] | None = None
    asset_id: str | None = None
    resource_id: Identifier | None = None
    storage_key: str | None = None
    content: str | None = None


class CanvasTaskBindRead(CanvasGenerationProtocol):
    op: Literal["canvas.task.bind"] = "canvas.task.bind"
    op_id: str
    replayed: bool
    result: CanvasTaskBindReceipt
