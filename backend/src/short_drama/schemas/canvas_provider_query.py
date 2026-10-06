"""原版失败视频任务的只查询与取回响应。"""

from typing import Literal

from .canvas_generation import CanvasGenerationProtocol
from .canvas_task_runtime import CanvasRuntimeTaskRead


class CanvasProviderTaskQueryRead(CanvasGenerationProtocol):
    task: CanvasRuntimeTaskRead
    provider_status: Literal["processing", "failed", "succeeded"]
    recovered: bool
