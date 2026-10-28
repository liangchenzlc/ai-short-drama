"""固定源本地转写的字幕结果；毫秒保持有符号 int64。"""

from typing import Annotated

from pydantic import ConfigDict, Field, StrictStr

from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.canvas.document.canvas import CanvasKey
from short_drama.schemas.canvas.generation.generation import CanvasGenerationProtocol
from short_drama.schemas.canvas.generation.tasks.task_runtime import OperationId


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
    model_config_id: Identifier | None = None
    project_id: CanvasKey | None = None
    client_operation_id: OperationId


class CanvasTranscriptionModelRead(InputModel):
    id: Identifier
    display_name: str = Field(alias="displayName")


class CanvasTranscriptionModelsRead(InputModel):
    models: list[CanvasTranscriptionModelRead]
