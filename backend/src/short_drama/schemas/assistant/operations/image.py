"""Task-independent image identities; no locator or runtime identity is accepted."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, parse_identifier
from short_drama.schemas.media.image_groups.groups import ImagePrimarySet


class ImageTarget(InputModel):
    project_id: Identifier = Field(description="获准作品 ID；来自当前任务目标。")
    target_kind: Literal["asset", "shot"] = Field(
        description="图片所属主体类型：角色/场景/道具素材 asset，或分镜镜头 shot。"
    )
    target_id: Identifier = Field(
        description="目标素材/镜头的真实 ID；来自已读取并授权的主体目录。"
    )


class ReadImageGroup(ImageTarget):
    group_id: Identifier | None = Field(
        default=None, description="可选的真实图片组 ID；来自该目标的图组目录。"
    )
    view: Literal["current", "detail", "history"] = Field(
        default="current", description="current 读取当前图组，detail/history 读取相应历史目录视图。"
    )
    offset: Annotated[
        int, Field(ge=0, le=100000, description="图片组列表偏移，从 0 开始；用 next 中的值继续。")
    ] = 0
    limit: Annotated[int, Field(ge=1, le=10, description="本页最多返回的图片组数量。")] = 5
    character_offset: Annotated[
        int, Field(ge=0, le=1048576, description="当前页 JSON 结果的字符偏移；长目录按 next 续读。")
    ] = 0
    character_limit: Annotated[
        int, Field(ge=1, le=6000, description="本次最多返回的目录 JSON 字符数。")
    ] = 4000


class ReadImageContent(InputModel):
    project_id: Identifier = Field(description="获准作品 ID；来自当前任务目标。")
    target_kind: Literal["asset", "shot", "canvas_node"] = Field(
        description="来源类型；canvas_node 使用画布节点字段，asset/shot 使用图组图位字段。"
    )
    target_id: Annotated[
        str,
        Field(
            min_length=1, max_length=128, description="获准素材/镜头 ID，或当前画布中的节点 key。"
        ),
    ]
    canvas_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="target_kind=canvas_node 时的当前获准画布 key。",
    )
    group_id: Identifier | None = Field(
        default=None, description="asset/shot 图片所属图组 ID；从 read_image_group 返回值取得。"
    )
    item_id: Identifier | None = Field(
        default=None, description="asset/shot 图组中的图位 ID；从 read_image_group 返回值取得。"
    )
    media_id: Identifier = Field(description="要查看的图片媒体 ID；必须属于该主体/节点的冻结目录。")
    purpose: Annotated[
        str, Field(min_length=1, max_length=300, description="说明要从图片像素核对什么。")
    ]
    detail: Literal["low", "high", "original"] = Field(
        default="low", description="请求图像像素的细节级别。"
    )
    crop: tuple[int, int, int, int] | None = Field(
        default=None, description="可选裁剪框 (left, top, right, bottom)，图像坐标，需有正宽高。"
    )

    @model_validator(mode="after")
    def concrete_image_identity(self):
        if self.target_kind == "canvas_node":
            if self.canvas_id is None or self.group_id is not None or self.item_id is not None:
                raise ValueError("canvas images require canvas_id and do not use image groups")
        else:
            parse_identifier(self.target_id)
            if self.canvas_id is not None or self.group_id is None or self.item_id is None:
                raise ValueError("asset and shot images require their group and item identities")
        return self


class SetImageGroupPrimary(ImagePrimarySet, ImageTarget):
    group_id: Identifier = Field(
        description="待采用图片所属图组 ID；来自 read_image_group 返回值。"
    )
