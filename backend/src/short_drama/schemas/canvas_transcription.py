"""固定源本地转写的字幕结果；毫秒保持有符号 int64。"""

from typing import Annotated

from pydantic import ConfigDict, Field, StrictStr

from .base import Identifier, InputModel
from .canvas import CanvasKey
from .canvas_generation import CanvasGenerationProtocol
from .canvas_task_runtime import OperationId


class CanvasTranscriptionSegment(InputModel):
    model_config = ConfigDict(populate_by_name=True)

    start_ms: Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)] = Field(alias="startMs")
    end_ms: Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)] = Field(alias="endMs")
    text: StrictStr


class CanvasTranscriptionResult(InputModel):
    segments: list[CanvasTranscriptionSegment]
    srt: StrictStr
    language: StrictStr


class CanvasTranscriptionCreate(CanvasGenerationProtocol):
    resource_id: Identifier
    language: StrictStr = Field(default="", max_length=128)
    project_id: CanvasKey | None = None
    client_operation_id: OperationId
