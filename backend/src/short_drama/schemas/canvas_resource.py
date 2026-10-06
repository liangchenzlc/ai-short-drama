"""BeefTV resource wire names are aliases at the Python boundary."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from .base import Identifier, InputModel

ResourceKind = Literal["image", "video", "audio", "file"]
Dimension = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]


class CanvasResourceStart(InputModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    file_name: str = Field(alias="fileName", min_length=1, max_length=255)
    kind: ResourceKind
    size: int = Field(strict=True, gt=0, le=5 * 1024**4)
    width: Dimension | None = None
    height: Dimension | None = None
    duration_ms: int | None = Field(
        default=None, alias="durationMs", strict=True, ge=0, le=2**53 - 1
    )
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", max_length=512)


class CanvasResourceRead(InputModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Identifier
    user_id: Identifier = Field(alias="userId")
    kind: ResourceKind
    status: Literal["ready"] = "ready"
    provider: Literal["minio"] = "minio"
    endpoint: str = ""
    bucket: str = ""
    object_key: str = Field(default="", alias="objectKey")
    public_url: str = Field(default="", alias="publicUrl")
    mime_type: str = Field(alias="mimeType")
    size: int
    width: int = 0
    height: int = 0
    duration_ms: int = Field(default=0, alias="durationMs")
    etag: str = ""
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class CanvasResourceEnvelope(InputModel):
    resource: CanvasResourceRead


class CanvasResourceCopyRequest(InputModel):
    source_resource_id: Identifier
    canvas_key: str = Field(min_length=1, max_length=64)


class CanvasResourceNormalizeRequest(InputModel):
    canvas_key: str = Field(min_length=1, max_length=64)
    resource_ids: list[Identifier] = Field(min_length=1, max_length=200)


class CanvasResourceNormalizeRead(InputModel):
    resource_map: dict[str, Identifier]
    resource_aliases: dict[str, list[Identifier]]


class CanvasUploadRead(InputModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    upload_id: Identifier = Field(alias="uploadId")
    chunk_size: int = Field(alias="chunkSize")
    chunk_count: int = Field(alias="chunkCount")


class CanvasChunkRead(InputModel):
    index: int
