"""Bounded canvas actions; model input never contains a replacement document or media URL."""

import json
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion
from short_drama.schemas.canvas.document.canvas import CanvasPosition
from short_drama.schemas.canvas.generation.generation_parameters import CanvasAudioProviderOptions

NodeReference = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=128, pattern=r"^(?:\$[A-Za-z][\w-]{0,47}|[\w:.-]+)$"
    ),
]
TemporaryNodeKey = Annotated[
    str, StringConstraints(min_length=1, max_length=48, pattern=r"^[A-Za-z][\w-]*$")
]
CanvasNodeKind = Literal["text", "image", "video", "audio"]
NodeText = Annotated[str, Field(max_length=32000)]


class AssistantNodeSettings(InputModel):
    size: str | None = Field(
        default=None, max_length=64, description="图片生成尺寸/比例参数；须符合目标节点模型能力。"
    )
    quality: str | None = Field(
        default=None, max_length=64, description="图片生成质量预设；须符合模型能力目录。"
    )
    seconds: str | None = Field(
        default=None,
        pattern=r"^[1-9][0-9]{0,3}$",
        description="视频时长秒数的十进制字符串；仅用于支持该设置的视频节点。",
    )
    vquality: str | None = Field(
        default=None, max_length=64, description="视频质量预设；须符合所选模型能力。"
    )
    audioVoice: str | None = Field(
        default=None, max_length=255, description="音频合成音色名称或模型支持的 voice 值。"
    )
    audioFormat: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] | None = Field(
        default=None, description="音频输出格式；仅可选枚举值。"
    )
    audioSpeed: str | None = Field(
        default=None, max_length=16, description="音频语速字符串；数值范围按模型原生约束。"
    )
    audioInstructions: str | None = Field(
        default=None, max_length=4096, description="音频生成指令，例如语气、音色表现或发音要求。"
    )

    @field_validator("audioSpeed")
    @classmethod
    def native_speed_range(cls, value):
        if value is not None:
            CanvasAudioProviderOptions(speed=float(value))
        return value


class AssistantNodeValues(InputModel):
    type: CanvasNodeKind = Field(description="新节点类型：text、image、video 或 audio。")
    title: Annotated[str, Field(max_length=255, description="节点显示标题。")] = ""
    content: NodeText = Field(
        default="", description="text 节点正文；媒体节点不能由模型提供结果内容或 URL。"
    )
    prompt: NodeText = Field(
        default="", description="媒体节点下一次生成使用的提示词；不会自动提交生成。"
    )
    model_config_id: Identifier | None = Field(
        default=None, description="可选的本人模型配置 ID；须来自当前可用模型目录。"
    )
    settings: AssistantNodeSettings = Field(
        default_factory=AssistantNodeSettings, description="与节点类型对应的下一次媒体生成设置。"
    )

    @model_validator(mode="after")
    def text_content_only(self):
        if self.type != "text" and self.content:
            raise ValueError("media nodes cannot receive model-supplied content or result URLs")
        return self


class AssistantNodeUpdate(InputModel):
    title: Annotated[
        str, Field(max_length=255, description="要替换的节点标题；省略表示不修改。")
    ] = None
    content: NodeText = Field(
        default=None, description="text 节点新正文；媒体节点不能设置结果 URL。"
    )
    prompt: NodeText = Field(default=None, description="要替换的生成提示词；省略表示不修改。")
    model_config_id: Identifier | None = Field(
        default=None, description="要设置的模型配置 ID；显式 null 清除，省略表示不修改。"
    )
    settings: AssistantNodeSettings = Field(
        default=None, description="完整替换的节点生成设置；省略表示不修改。"
    )

    @model_validator(mode="after")
    def explicit_values(self):
        if not self.model_fields_set:
            raise ValueError("node update requires a field")
        if any(
            getattr(self, field) is None for field in self.model_fields_set - {"model_config_id"}
        ):
            raise ValueError("node fields cannot be null")
        return self


class CreateAssistantNode(InputModel):
    action: Literal["create_node"] = Field(description="动作分支标识；必须是 create_node。")
    key: TemporaryNodeKey = Field(description="本批新节点临时键；后续动作通过 $<key> 引用。")
    values: AssistantNodeValues = Field(description="新节点的类型、标题、内容/提示词和设置。")


class UpdateAssistantNode(InputModel):
    action: Literal["update_node"] = Field(description="动作分支标识；必须是 update_node。")
    node: NodeReference = Field(description="已有节点身份，或本批更早创建的 $key。")
    row_version: Identifier = Field(description="节点公共 row_version；来自最近读取的画布节点。")
    private_revision: NonnegativeVersion = Field(
        description="当前用户对此节点的 private_revision；来自同一节点读取，未保存私人状态时为 0。"
    )
    patch: AssistantNodeUpdate = Field(description="需修改的节点字段；仅传改变项。")


class MoveAssistantNode(InputModel):
    action: Literal["move_node"] = Field(description="动作分支标识；必须是 move_node。")
    node: NodeReference = Field(description="已有节点身份，或本批更早创建的 $key。")
    row_version: Identifier = Field(description="节点公共 row_version；来自最近读取的节点。")
    private_revision: NonnegativeVersion = Field(
        description="当前用户私人状态版本；来自读取结果，未保存时为 0。"
    )
    position: CanvasPosition = Field(description="节点画布坐标 x/y。")


class DeleteAssistantNode(InputModel):
    action: Literal["delete_node"] = Field(description="动作分支标识；必须是 delete_node。")
    node: NodeReference = Field(description="要删除的已有节点或本批临时节点 $key。")
    row_version: Identifier = Field(description="节点公共 row_version；来自最近读取的节点。")
    private_revision: NonnegativeVersion = Field(
        description="当前用户私人状态版本；来自读取结果，未保存时为 0。"
    )


class ConnectAssistantNodes(InputModel):
    action: Literal["connect_nodes", "disconnect_nodes"] = Field(
        description="连线操作：connect_nodes 创建连接，disconnect_nodes 移除连接。"
    )
    source: NodeReference = Field(description="连线起点节点 ID 或本批更早创建的 $key。")
    target: NodeReference = Field(
        description="连线终点节点 ID 或本批更早创建的 $key；不能与 source 相同。"
    )

    @model_validator(mode="after")
    def distinct_endpoints(self):
        if self.source == self.target:
            raise ValueError("self connections are not supported")
        return self


CanvasAction = Annotated[
    CreateAssistantNode
    | UpdateAssistantNode
    | MoveAssistantNode
    | DeleteAssistantNode
    | ConnectAssistantNodes,
    Field(discriminator="action"),
]


class AssistantCanvasPatch(InputModel):
    expected_revision: Identifier = Field(
        description="目标画布当前 row_version；来自最近一次画布读取。"
    )
    actions: Annotated[
        list[CanvasAction],
        Field(
            min_length=1,
            max_length=100,
            description="按顺序原子应用的节点/连线动作；动作通过 action 判别。",
        ),
    ]

    @model_validator(mode="after")
    def bounded_increment(self):
        known, references, created = set(), [], 0
        for action in self.actions:
            if action.action == "create_node":
                if action.key in known:
                    raise ValueError("temporary node keys must be unique")
                known.add(action.key)
                created += 1
            else:
                references = (
                    [action.source, action.target]
                    if action.action in {"connect_nodes", "disconnect_nodes"}
                    else [action.node]
                )
                if any(ref.startswith("$") and ref[1:] not in known for ref in references):
                    raise ValueError("temporary references must identify earlier nodes")
        if created > 50:
            raise ValueError("one patch can create at most 50 nodes")
        if len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode()) > 1048576:
            raise ValueError("canvas increment exceeds 1 MiB")
        return self


class ApplyCanvasPatch(InputModel):
    project_id: Identifier = Field(description="当前获准画布所属项目 ID。")
    canvas_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            description="当前任务目标中的画布 key，不是数据库 canvas_id。",
        ),
    ]
    patch: AssistantCanvasPatch = Field(description="带画布预期修订和有界 actions 的增量写入。")
