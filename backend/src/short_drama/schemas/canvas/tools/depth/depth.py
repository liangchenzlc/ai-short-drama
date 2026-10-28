"""固定源深度动作捕捉的准入与永久结果；任务身份保持十进制字符串。"""

from typing import Annotated, Literal

from pydantic import Field, StrictStr

from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas.document.canvas import CanvasKey
from short_drama.schemas.canvas.generation.generation import CanvasGenerationProtocol
from short_drama.schemas.canvas.generation.tasks.task_runtime import OperationId

DepthInteger = Annotated[int, Field(strict=True, gt=0)]


class CanvasDepthCaptureCreate(CanvasGenerationProtocol):
    resource_id: Identifier
    project_id: CanvasKey | None = None
    client_operation_id: OperationId


class CanvasDepthCaptureResult(CanvasGenerationProtocol):
    resource_id: Identifier
    file_name: Literal["depth-action-reference.mp4"] = "depth-action-reference.mp4"
    size: DepthInteger
    duration_ms: DepthInteger
    width: Literal[1920] = 1920
    height: Literal[1080] = 1080
    device: Literal["cpu", "cuda", "mps"]
    fallback_reason: StrictStr = ""
    runtime_version: Literal["v1"] = "v1"
    processing_ms: Annotated[int, Field(strict=True, ge=0)] = 0
