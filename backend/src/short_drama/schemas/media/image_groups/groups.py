from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel

OperationType = Literal[
    "crop",
    "annotation",
    "resize",
    "split",
    "replace",
    "mask",
    "emotion",
    "texture",
    "angle",
    "lighting",
    "reverse_prompt",
]


class ImagePrimarySet(InputModel):
    item_id: Identifier
    expected_primary_version: Identifier
    expected_item_version: Identifier
    expected_target_version: Identifier


class ImageToolParameters(InputModel):
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    width: float = Field(default=1, gt=0, le=1)
    height: float = Field(default=1, gt=0, le=1)
    output_width: int | None = Field(default=None, strict=True, ge=1, le=4096)
    output_height: int | None = Field(default=None, strict=True, ge=1, le=4096)
    rows: int = Field(default=2, strict=True, ge=1, le=5)
    columns: int = Field(default=2, strict=True, ge=1, le=5)
    interpolation: Literal["nearest", "bilinear", "high"] = "high"
    instructions: str = Field(default="", max_length=4000)
    preset: Annotated[str, Field(max_length=64)] = ""
    horizontal_angle: float = Field(default=45, ge=-180, le=180)
    pitch_angle: float = Field(default=0, ge=-90, le=90)
    camera_distance: float = Field(default=4.8, ge=1, le=10)
    wide_angle: bool = False
    azimuth: float = Field(default=0, ge=0, le=360)
    elevation: float = Field(default=0, ge=-90, le=90)
    brightness: int = Field(default=50, strict=True, ge=0, le=100)
    light_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    rim_light: bool = False
    skin: int = Field(default=50, strict=True, ge=0, le=100)
    texture: int = Field(default=50, strict=True, ge=0, le=100)
    sharpness: int = Field(default=50, strict=True, ge=0, le=100)
    light_blend: int = Field(default=50, strict=True, ge=0, le=100)
    scene_blend: int = Field(default=50, strict=True, ge=0, le=100)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("选区必须位于图片内")
        return self


class ImageOperationCreate(InputModel):
    item_id: Identifier
    expected_item_version: Identifier
    operation_type: OperationType
    parameters: ImageToolParameters = Field(default_factory=ImageToolParameters)
    restore_media_id: Identifier | None = None

    @model_validator(mode="after")
    def operation_parameters(self):
        p = self.parameters
        if self.operation_type == "split" and p.rows * p.columns < 2:
            raise ValueError("宫格切分至少需要两格")
        if self.operation_type == "resize" and (not p.output_width or not p.output_height):
            raise ValueError("调整尺寸需要完整的目标宽高")
        if self.operation_type == "mask" and not p.instructions.strip():
            raise ValueError("请填写局部重绘要求")
        if self.restore_media_id and self.operation_type != "replace":
            raise ValueError("仅替换操作可恢复原图")
        return self


class ImageOperationSource(InputModel):
    scene: Literal["image_operation"]
    operation_id: Identifier


class ImageMediaRead(ReadModel):
    media_id: Identifier
    url: str | None
    width: int | None
    height: int | None


class ImageGroupItemRead(ReadModel):
    id: Identifier
    output_index: int
    media_id: Identifier | None
    url: str | None
    width: int | None
    height: int | None
    item_version: Identifier
    status: Literal["ready", "generating", "failed"]
    error: str | None
    prompt: str | None
    history: list[ImageMediaRead]


class ImageGroupRead(ReadModel):
    id: Identifier
    kind: str
    status: Literal["generating", "completed", "partial", "failed"]
    is_current: bool
    target_kind: Literal["asset", "shot"]
    target_id: Identifier
    target_version: Identifier
    expected_count: int
    primary_index: int | None
    primary_version: Identifier
    notice: str | None
    created_at: datetime
    task_id: Identifier | None
    items: list[ImageGroupItemRead]


class ImageGroupPage(ReadModel):
    items: list[ImageGroupRead]
    total: int
    offset: int
    limit: int


class ImageOperationRead(ReadModel):
    id: Identifier
    item_id: Identifier
    operation_type: OperationType
    status: Literal["pending", "prepared", "running", "completed", "conflict", "failed"]
    task_id: Identifier | None
    result_media_id: Identifier | None
    result_group_id: Identifier | None
    mask_media_id: Identifier | None
