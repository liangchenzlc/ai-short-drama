"""Three closed generation inputs; callers cannot select transport or credentials."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from .base import Identifier, InputModel, nonblank

Prompt = Annotated[str, Field(min_length=1, max_length=1048576), AfterValidator(nonblank)]
Aspect = Literal["16:9", "9:16", "1:1", "4:3", "3:4"]


class ShotImageSource(InputModel):
    scene: Literal["shot_image"]
    shot_id: Identifier
    layout: Literal["single", "four", "five", "nine"]


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
    input: TextInput
    parameters: TextParameters = Field(default_factory=TextParameters)
    source: None = None


class ImageInput(InputModel):
    prompt: Prompt
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
