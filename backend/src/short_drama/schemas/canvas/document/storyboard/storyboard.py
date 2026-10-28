"""画布分镜的闭合上下文与模型输出；模型不能决定持久身份或媒体关联。"""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from short_drama.schemas.canvas.document.canvas import SourceKey
from short_drama.schemas.canvas.generation.generation import CanvasGenerationProtocol

Text = Annotated[str, Field(strict=True, max_length=8000)]


class CanvasStoryboardStyle(CanvasGenerationProtocol):
    preset_id: Annotated[str, Field(min_length=1, max_length=128)]
    title: Annotated[str, Field(min_length=1, max_length=255)]
    prompt: Annotated[str, Field(min_length=1, max_length=16000)]
    profile_json: Annotated[str, Field(max_length=64000)] | None = None


class CanvasStoryboardCharacter(CanvasGenerationProtocol):
    node_id: SourceKey
    name: Annotated[str, Field(min_length=1, max_length=255)]
    definition: dict[str, JsonValue]


class CanvasStoryboardAsset(CanvasGenerationProtocol):
    id: SourceKey
    title: Annotated[str, Field(min_length=1, max_length=121)]
    type: Literal["image", "video", "audio", "character"]
    category: Annotated[str, Field(max_length=128)] | None = None
    tags: Annotated[list[Annotated[str, Field(max_length=65)]], Field(max_length=12)]
    prompt: Annotated[str, Field(max_length=601)]


class CanvasStoryboardAssetBinding(CanvasGenerationProtocol):
    node_id: SourceKey
    role: Literal[
        "character", "environment", "wardrobe", "prop", "weapon", "style", "motion", "audio"
    ]
    priority: Annotated[int, Field(strict=True, ge=0, le=100)] = 0


class CanvasStoryboardRowResult(CanvasGenerationProtocol):
    shot_number: Annotated[int, Field(strict=True, ge=1, le=100)]
    duration_seconds: Annotated[int, Field(strict=True, ge=1, le=60)]
    plot_description: Annotated[str, Field(strict=True, min_length=1, max_length=8000)]
    dialogue: Text = ""
    characters: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=255)]], Field(max_length=60)
    ] = Field(default_factory=list)
    narrative_intent: Text = ""
    viewer_pov: Text = Field(default="", alias="viewerPOV")
    performance_blocking: Text = ""
    shot_size: Text = ""
    emotion: Text = ""
    lighting_and_atmosphere: Text = ""
    audio_effects: Text = ""
    camera: Text = ""
    motion: Text = ""
    time_beats: Text = ""
    image_generation_prompt: Text = ""
    video_motion_prompt: Text = ""
    must_have: Annotated[list[Text], Field(max_length=3)] = Field(default_factory=list)
    optional_details: Annotated[list[Text], Field(max_length=30)] = Field(default_factory=list)
    continuity_out: Text = ""
    negative_prompt: Text = ""
    asset_bindings: Annotated[list[CanvasStoryboardAssetBinding], Field(max_length=60)] = Field(
        default_factory=list
    )


class CanvasStoryboardResult(CanvasGenerationProtocol):
    title: Annotated[str, Field(strict=True, min_length=1, max_length=255)]
    rows: Annotated[list[CanvasStoryboardRowResult], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def sequential_rows(self):
        if not self.title.strip() or any(not row.plot_description.strip() for row in self.rows):
            raise ValueError("Storyboard title and shot descriptions must contain text")
        if [row.shot_number for row in self.rows] != list(range(1, len(self.rows) + 1)):
            raise ValueError("Storyboard shot numbers must follow row order")
        return self
