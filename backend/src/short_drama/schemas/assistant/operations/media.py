"""Closed assistant media inputs; account, project and generation identities are trusted."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, nonblank
from short_drama.schemas.canvas.document.canvas import SourceKey
from short_drama.schemas.generations.admission.generation import Aspect

MEDIA_USAGE_KEYS = {"image": "images", "video": "videos", "audio": "audio"}


class AssistantImageRequest(InputModel):
    target_kind: Literal["asset", "shot"] = Field(
        description="图片目标类型：素材 asset 或分镜 shot。"
    )
    media_kind: Literal["image"] = Field(
        default="image", description="此请求生成图片；固定为 image。"
    )
    target_id: Identifier = Field(description="素材或分镜真实 ID；必须在当前授权范围。")
    row_version: Identifier = Field(description="目标素材/分镜当前 row_version；来自最近读取结果。")
    model_config_id: Identifier = Field(
        description="本人启用的图片模型配置 ID；来自目标描述返回的模型目录。"
    )
    model_version: Identifier = Field(
        description="对应模型配置当前 row_version；来自同一模型目录项。"
    )
    prompt: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4000,
            description="本次图片生成的补充提示词；主体设定由服务端读取。",
        ),
        AfterValidator(nonblank),
    ]
    count: Annotated[
        int, Field(strict=True, ge=1, le=4, description="本目标希望生成的图片张数。")
    ] = 1
    aspect: Aspect | None = Field(
        default=None, description="可选图片画幅比例；null 表示使用模型/项目默认值。"
    )
    resolution: Annotated[
        str, Field(min_length=1, max_length=32, description="图片分辨率预设；须符合目标模型能力。")
    ] = "2K"
    reference_media_ids: Annotated[
        list[Identifier],
        Field(max_length=16, description="本次可使用的真实图片媒体 ID；来自获准图片目录。"),
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_references(self):
        if len(set(self.reference_media_ids)) != len(self.reference_media_ids):
            raise ValueError("Reference images must be unique")
        return self


class AssistantCanvasMediaRequest(InputModel):
    target_kind: Literal["node"] = Field(description="画布节点目标；固定为 node。")
    target_id: SourceKey = Field(description="当前获准画布中的节点 key；不是分镜或数据库 ID。")
    row_version: Identifier = Field(description="节点公共 row_version；来自最近画布读取。")
    private_revision: NonnegativeVersion = Field(
        description="当前用户对此节点的 private_revision；未建私人状态时为 0。"
    )
    media_kind: Literal["image", "video", "audio"] = Field(
        default="image", description="节点本次生成的媒体类型。"
    )
    model_config_id: Identifier = Field(
        description="与 media_kind 匹配的本人启用模型 ID；来自目标模型目录。"
    )
    model_version: Identifier = Field(description="所选模型配置 row_version；来自同一目录项。")
    prompt: Annotated[
        str,
        Field(min_length=1, max_length=4000, description="本次节点媒体生成提示词。"),
        AfterValidator(nonblank),
    ]
    count: Annotated[
        int, Field(strict=True, ge=1, le=4, description="生成张数；video/audio 固定为 1。")
    ] = 1
    reference_media_ids: Annotated[
        list[Identifier],
        Field(max_length=16, description="本次获准使用的图片媒体 ID；音频不能引用图片。"),
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_outputs(self):
        if self.media_kind != "image" and self.count != 1:
            raise ValueError("Native video and audio nodes produce one result per request")
        if self.media_kind == "audio" and self.reference_media_ids:
            raise ValueError("Audio generation does not accept image references")
        if len(set(self.reference_media_ids)) != len(self.reference_media_ids):
            raise ValueError("Reference images must be unique")
        return self


class AssistantNativeVoiceVersion(InputModel):
    character_id: Identifier = Field(description="对白中角色的真实素材 ID。")
    row_version: Identifier = Field(description="角色素材 row_version；来自角色读取结果。")
    voice_version: Identifier = Field(description="角色已采用音色版本；来自当前配音读取结果。")


class AssistantShotVideoRequest(InputModel):
    target_kind: Literal["shot_video"] = Field(description="标准分镜视频目标；固定为 shot_video。")
    target_id: Identifier = Field(description="真实分镜 ID；必须属于当前授权项目。")
    row_version: Identifier = Field(description="分镜 row_version；来自最近分镜读取。")
    media_kind: Literal["video"] = Field(
        default="video", description="此请求生成视频；固定为 video。"
    )
    model_config_id: Identifier = Field(
        description="本人启用的视频模型配置 ID；来自目标描述模型目录。"
    )
    model_version: Identifier = Field(description="视频模型配置 row_version；来自同一模型目录项。")
    prompt: Annotated[
        str,
        Field(min_length=1, max_length=4000, description="该镜头视频生成的补充提示词。"),
        AfterValidator(nonblank),
    ]
    count: Annotated[
        int,
        Field(strict=True, ge=1, le=1, description="分镜视频每个请求只能生成一个结果，固定为 1。"),
    ] = 1
    reference_media_ids: Annotated[
        list[Identifier],
        Field(
            min_length=1,
            max_length=1,
            description="该镜头当前采用的首帧图片媒体 ID；来自图组/主图读取。",
        ),
    ]
    native_dialogue_version: NonnegativeVersion | None = Field(
        default=None, description="使用原生对白生成有声视频时的对白文档版本；无此模式时省略。"
    )
    native_voice_versions: Annotated[
        list[AssistantNativeVoiceVersion],
        Field(max_length=2, description="有声视频使用的角色音色身份及版本；需与对白版本配套。"),
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def native_sources(self):
        if self.native_dialogue_version is None and self.native_voice_versions:
            raise ValueError("Native voices require an explicit dialogue version")
        identifiers = [voice.character_id for voice in self.native_voice_versions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Native voice characters must be unique")
        return self


class AssistantCharacterVoiceRequest(InputModel):
    target_kind: Literal["character_voice"] = Field(
        description="角色音色设计目标；固定为 character_voice。"
    )
    target_id: Identifier = Field(description="角色素材 ID；来自当前获准角色目录。")
    row_version: Identifier = Field(description="角色素材 row_version；来自最近读取。")
    voice_version: NonnegativeVersion = Field(description="当前采用音色版本；无已采用音色时为 0。")
    media_kind: Literal["audio"] = Field(
        default="audio", description="角色音色生成固定使用 audio。"
    )
    model_config_id: Identifier = Field(
        description="本人启用音频模型配置 ID；来自目标描述模型目录。"
    )
    model_version: Identifier = Field(description="音频模型配置 row_version；来自同一目录项。")
    prompt: Annotated[
        str,
        Field(min_length=1, max_length=500, description="音色设计提示词。"),
        AfterValidator(nonblank),
    ]
    preview_text: Annotated[
        str,
        Field(min_length=15, max_length=200, description="用于评估音色的试听文本。"),
        AfterValidator(nonblank),
    ]
    count: Annotated[
        int, Field(strict=True, ge=1, le=1, description="音色预览每个请求固定生成一个结果。")
    ] = 1
    reference_media_ids: Annotated[
        list[Identifier], Field(max_length=0, description="角色音色不接受参考图片，必须为空。")
    ] = Field(default_factory=list)


class AssistantDialogueAudioRequest(InputModel):
    target_kind: Literal["dialogue_audio"] = Field(
        description="声音时间线台词配音目标；固定为 dialogue_audio。"
    )
    target_id: Annotated[
        str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$", description="声音时间线中真实台词行身份。")
    ]
    row_version: Identifier = Field(description="台词行 row_version；来自当前声音时间线读取。")
    assembly_version: Identifier = Field(description="所属成片时间线版本；来自同一声音快照。")
    timeline_hash: Annotated[
        str, Field(pattern=r"^[a-f0-9]{64}$", description="同一声音快照返回的 timeline_hash。")
    ]
    media_kind: Literal["audio"] = Field(default="audio", description="台词配音固定生成 audio。")
    model_config_id: Identifier = Field(
        description="本人启用音频模型配置 ID；来自目标描述模型目录。"
    )
    model_version: Identifier = Field(description="音频模型配置 row_version；来自同一目录项。")
    count: Annotated[
        int, Field(strict=True, ge=1, le=1, description="单条台词配音固定生成一个结果。")
    ] = 1
    reference_media_ids: Annotated[
        list[Identifier], Field(max_length=0, description="台词配音不接受图片引用，必须为空。")
    ] = Field(default_factory=list)


class SubmitGenerationBatch(InputModel):
    project_id: Identifier = Field(description="已获准项目 ID；来自当前任务目标。")
    canvas_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="画布媒体请求所属的画布 key；标准项目请求省略。",
    )
    requests: Annotated[
        list[
            Annotated[
                AssistantImageRequest
                | AssistantCanvasMediaRequest
                | AssistantShotVideoRequest
                | AssistantCharacterVoiceRequest
                | AssistantDialogueAudioRequest,
                Field(discriminator="target_kind"),
            ]
        ],
        Field(
            min_length=1,
            max_length=16,
            description="一个或多个不同目标的媒体生成请求；target_kind 决定每项字段结构。",
        ),
    ]

    @model_validator(mode="after")
    def unique_targets(self):
        targets = [(item.target_kind, item.target_id) for item in self.requests]
        if len(set(targets)) != len(targets):
            raise ValueError("One batch can generate only one image group per target")
        return self


class GetGenerationStatus(InputModel):
    project_id: Identifier = Field(description="原受理回执中的项目 ID；须与任务所属项目一致。")
    generation_ids: Annotated[
        list[Identifier],
        Field(
            min_length=1,
            max_length=16,
            description="原媒体提交回执返回的 generation_id 列表；不是 async task_id。",
        ),
    ]
