from typing import Literal

from pydantic import Field, model_validator

from .base import Identifier, InputModel


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
    character_id: Identifier
    text: str = Field(min_length=1, max_length=500)
    delivery: str = Field(default="", max_length=200)
    speech: Literal["onscreen", "voiceover"] = "onscreen"


class NativeDialogueDocument(InputModel):
    lines: list[NativeLine] = Field(default_factory=list, max_length=20)
    reviewed: bool = False

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
