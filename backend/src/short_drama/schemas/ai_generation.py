"""Three closed generation inputs; callers cannot select transport or credentials."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from .base import Identifier, InputModel, nonblank

Prompt = Annotated[str, Field(min_length=1, max_length=1048576), AfterValidator(nonblank)]
Aspect = Literal["16:9", "9:16", "1:1", "4:3", "3:4"]


class ShotImageSource(InputModel):
    scene: Literal["shot_image"]
    shot_id: Identifier
    layout: Literal["single", "four", "five", "nine"]
    context_mode: Literal["saved"] | None = None
    row_version: Identifier | None = None
    context_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None

    @model_validator(mode="after")
    def context_tokens(self):
        if self.context_mode == "saved":
            if self.row_version is None or self.context_hash is None:
                raise ValueError("Saved context requires version and hash")
        elif self.row_version is not None or self.context_hash is not None:
            raise ValueError("Context tokens require saved mode")
        return self


class NovelScriptSource(InputModel):
    scene: Literal["novel_script"]
    project_id: Identifier
    episode_id: Identifier
    content_version: Identifier


class ScriptShotsSource(InputModel):
    scene: Literal["script_shots"]
    project_id: Identifier
    episode_id: Identifier
    script_id: Identifier
    content_version: Identifier


class ScriptAssetsSource(ScriptShotsSource):
    scene: Literal["script_assets"]


class ExtractionOptions(InputModel):
    kinds: list[Literal["character", "scene", "prop"]] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_kinds(self):
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("Extraction kinds must be unique")
        return self


class StoryboardOptions(InputModel):
    average_shot_duration_ms: int = Field(default=3000, strict=True, ge=1000, le=10000)


class TextMessage(InputModel):
    role: Literal["system", "user", "assistant"]
    content: Prompt


class TextInput(InputModel):
    messages: list[TextMessage] = Field(min_length=1, max_length=100)


class TextParameters(InputModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, strict=True, ge=1, le=1000000)


class TextGenerationCreate(InputModel):
    config_id: Identifier | None = None
    input: TextInput | None = None
    parameters: TextParameters = Field(default_factory=TextParameters)
    source: (
        Annotated[
            NovelScriptSource | ScriptShotsSource | ScriptAssetsSource, Field(discriminator="scene")
        ]
        | None
    ) = None
    instructions: str = Field(default="", max_length=4000)
    extraction: ExtractionOptions | None = None
    storyboard: StoryboardOptions | None = None

    @model_validator(mode="after")
    def business_or_generic(self):
        if self.source is not None and self.source.scene == "script_assets":
            if self.extraction is None:
                self.extraction = ExtractionOptions(kinds=["character", "scene", "prop"])
        elif self.extraction is not None:
            raise ValueError("Extraction options require script_assets source")
        if self.source is not None and self.source.scene == "script_shots":
            if self.storyboard is None:
                self.storyboard = StoryboardOptions()
        elif self.storyboard is not None:
            raise ValueError("Storyboard options require script_shots source")
        if self.source is None:
            if self.input is None or self.instructions:
                raise ValueError("Generic text requires messages and no business instructions")
        elif self.input is not None:
            raise ValueError("Business text uses saved content, not client messages")
        return self


class ImageInput(InputModel):
    prompt: str = Field(max_length=1048576)
    reference_media_ids: list[Identifier] = Field(default_factory=list, max_length=16)


class ImageParameters(InputModel):
    aspect: Aspect | None = None
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)] | None = None
    count: int = Field(default=1, strict=True, ge=1, le=4)


class ImageGenerationCreate(InputModel):
    config_id: Identifier | None = None
    input: ImageInput
    parameters: ImageParameters = Field(default_factory=ImageParameters)
    source: ShotImageSource | None = None

    @model_validator(mode="after")
    def business_prompt(self):
        if self.source and self.source.context_mode == "saved":
            if len(self.input.prompt) > 4000:
                raise ValueError("Supplement must be at most 4000 characters")
        elif not self.input.prompt.strip():
            raise ValueError("Prompt must not be blank")
        return self


class VideoInput(InputModel):
    prompt: Prompt
    first_frame_media_id: Identifier | None = None
    last_frame_media_id: Identifier | None = None


class VideoParameters(InputModel):
    aspect: Aspect | None = None
    resolution: Annotated[str, Field(max_length=32), AfterValidator(nonblank)] | None = None
    duration_ms: int | None = Field(default=None, strict=True, ge=1, le=3600000)


class VideoGenerationCreate(InputModel):
    config_id: Identifier | None = None
    input: VideoInput
    parameters: VideoParameters = Field(default_factory=VideoParameters)
    source: None = None


class GenerationRetry(InputModel):
    config_id: Identifier | None = None


GENERATION_SCHEMAS = {
    "text": TextGenerationCreate,
    "image": ImageGenerationCreate,
    "video": VideoGenerationCreate,
}
