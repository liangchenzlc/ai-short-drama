"""Closed assistant media inputs; account, project and generation identities are trusted."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, nonblank
from short_drama.schemas.canvas.document.canvas import SourceKey
from short_drama.schemas.generations.admission.generation import Aspect

MEDIA_USAGE_KEYS = {"image": "images", "video": "videos", "audio": "audio"}


class AssistantImageRequest(InputModel):
    target_kind: Literal["asset", "shot"]
    media_kind: Literal["image"] = "image"
    target_id: Identifier
    row_version: Identifier
    model_config_id: Identifier
    model_version: Identifier
    prompt: Annotated[str, Field(min_length=1, max_length=4000), AfterValidator(nonblank)]
    count: Annotated[int, Field(strict=True, ge=1, le=4)] = 1
    aspect: Aspect | None = None
    resolution: Annotated[str, Field(min_length=1, max_length=32)] = "2K"
    reference_media_ids: Annotated[list[Identifier], Field(max_length=16)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_references(self):
        if len(set(self.reference_media_ids)) != len(self.reference_media_ids):
            raise ValueError("Reference images must be unique")
        return self


class AssistantCanvasMediaRequest(InputModel):
    target_kind: Literal["node"]
    target_id: SourceKey
    row_version: Identifier
    private_revision: NonnegativeVersion
    media_kind: Literal["image", "video", "audio"] = "image"
    model_config_id: Identifier
    model_version: Identifier
    prompt: Annotated[str, Field(min_length=1, max_length=4000), AfterValidator(nonblank)]
    count: Annotated[int, Field(strict=True, ge=1, le=4)] = 1
    reference_media_ids: Annotated[list[Identifier], Field(max_length=16)] = Field(
        default_factory=list
    )

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
    character_id: Identifier
    row_version: Identifier
    voice_version: Identifier


class AssistantShotVideoRequest(InputModel):
    target_kind: Literal["shot_video"]
    target_id: Identifier
    row_version: Identifier
    media_kind: Literal["video"] = "video"
    model_config_id: Identifier
    model_version: Identifier
    prompt: Annotated[str, Field(min_length=1, max_length=4000), AfterValidator(nonblank)]
    count: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    reference_media_ids: Annotated[list[Identifier], Field(min_length=1, max_length=1)]
    native_dialogue_version: NonnegativeVersion | None = None
    native_voice_versions: Annotated[list[AssistantNativeVoiceVersion], Field(max_length=2)] = (
        Field(default_factory=list)
    )

    @model_validator(mode="after")
    def native_sources(self):
        if self.native_dialogue_version is None and self.native_voice_versions:
            raise ValueError("Native voices require an explicit dialogue version")
        identifiers = [voice.character_id for voice in self.native_voice_versions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Native voice characters must be unique")
        return self


class AssistantCharacterVoiceRequest(InputModel):
    target_kind: Literal["character_voice"]
    target_id: Identifier
    row_version: Identifier
    voice_version: NonnegativeVersion
    media_kind: Literal["audio"] = "audio"
    model_config_id: Identifier
    model_version: Identifier
    prompt: Annotated[str, Field(min_length=1, max_length=500), AfterValidator(nonblank)]
    preview_text: Annotated[str, Field(min_length=15, max_length=200), AfterValidator(nonblank)]
    count: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    reference_media_ids: Annotated[list[Identifier], Field(max_length=0)] = Field(
        default_factory=list
    )


class AssistantDialogueAudioRequest(InputModel):
    target_kind: Literal["dialogue_audio"]
    target_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    row_version: Identifier
    assembly_version: Identifier
    timeline_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    media_kind: Literal["audio"] = "audio"
    model_config_id: Identifier
    model_version: Identifier
    count: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    reference_media_ids: Annotated[list[Identifier], Field(max_length=0)] = Field(
        default_factory=list
    )


class SubmitGenerationBatch(InputModel):
    request_message_id: Identifier
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    retry_operation_id: Identifier | None = None
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
        Field(min_length=1, max_length=16),
    ]

    @model_validator(mode="after")
    def unique_targets(self):
        targets = [(item.target_kind, item.target_id) for item in self.requests]
        if len(set(targets)) != len(targets):
            raise ValueError("One batch can generate only one image group per target")
        return self


class GetGenerationStatus(InputModel):
    operation_id: Identifier
