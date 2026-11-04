from typing import Literal

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel


class AdoptedVoiceRead(ReadModel):
    media_id: Identifier
    url: str | None = None
    duration_ms: int | None = None
    row_version: int


class NativeVoiceRead(ReadModel):
    row_version: int
    record_id: Identifier | None = None
    current_voice: AdoptedVoiceRead | None = None
    candidates: list[dict]


class VoiceDesignSource(InputModel):
    scene: Literal["character_voice_design"]
    project_id: Identifier
    asset_id: Identifier
    voice_prompt: str = Field(min_length=1, max_length=500)
    preview_text: str = Field(min_length=15, max_length=200)


class VoiceAdopt(InputModel):
    row_version: int = Field(strict=True, ge=0)
    record_id: Identifier


class SoundModeEdit(InputModel):
    row_version: int = Field(strict=True, ge=0)
    mode: Literal["legacy", "native"]


class NativeLine(InputModel):
    character_id: Identifier = Field(
        description="说话角色素材 ID；使用当前项目已授权角色目录中的真实 ID。"
    )
    text: str = Field(min_length=1, max_length=500, description="该角色实际说出的台词文本。")
    delivery: str = Field(
        default="", max_length=200, description="可选表演/情绪提示，不是台词正文。"
    )
    speech: Literal["onscreen", "voiceover"] = Field(
        default="onscreen", description="onscreen 表示画面内说话，voiceover 表示画外音。"
    )


class NativeDialogueDocument(InputModel):
    lines: list[NativeLine] = Field(
        default_factory=list,
        max_length=20,
        description="按演出顺序排列的对白行；首版最多支持两位说话角色。",
    )
    reviewed: bool = Field(
        default=False,
        description="标记用户/创作者已明确核对该对白稿；不能由助手仅凭生成自动设为已核验。",
    )

    @model_validator(mode="after")
    def speakers(self):
        if len({line.character_id for line in self.lines}) > 2:
            raise ValueError("第一版仅支持最多两位角色轮流说话")
        if any(not line.text.strip() for line in self.lines):
            raise ValueError("台词不能为空白")
        return self


class NativeDialogueEdit(InputModel):
    row_version: int = Field(strict=True, ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    document: NativeDialogueDocument
