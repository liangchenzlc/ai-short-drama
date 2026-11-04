"""Versioned references shared by images, methods and their deeper resources."""

import json
import logging
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from short_drama.core.exceptions import WorkflowError

logger = logging.getLogger(__name__)
INLINE_MEDIA = re.compile(r"data:(?:image|audio|video)/[^\s\"']*;base64,", re.IGNORECASE)


class ResourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = Field(
        default=1, description="ResourceRef 格式版本；当前固定为 1。"
    )
    kind: Literal["image", "skill", "text", "script", "audio", "video", "file"] = Field(
        description="资源媒体/内容类别；由原始目录回执提供。"
    )
    ref_id: Annotated[
        str,
        Field(
            min_length=1, max_length=240, description="资源稳定身份；从受权目录或成功操作回执复制。"
        ),
    ]
    uri: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2048,
            description="受控资源引用 URI；只使用服务端返回值，不自行构造。",
        ),
    ]
    source: Annotated[
        str, Field(min_length=1, max_length=80, description="资源来源标识；沿用原始 ResourceRef。")
    ]
    version: str | None = Field(
        default=None, description="资源版本；按来源回执复制，未提供时为 null。"
    )
    checksum: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="可选资源 SHA-256 校验值；来自资源目录。",
    )
    metadata: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="服务端产生的资源说明元数据；原样交接，不填存储密钥或定位信息。",
    )
    availability: Literal["available", "unavailable", "stale"] = Field(
        default="available",
        description="资源当前可用状态；只沿用目录结果，不自行将不可用资源标记为可用。",
    )

    @model_validator(mode="after")
    def managed_uri(self):
        schemes = {"skill"} if self.kind == "skill" else {"minio", "artifact", "attachment"}
        if self.uri.split("://", 1)[0] not in schemes or "://" not in self.uri:
            raise ValueError("Only managed resource URIs are accepted")
        assert_reference_payload(self.metadata)
        return self


class HydrationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource: ResourceRef
    purpose: Annotated[str, Field(min_length=1, max_length=300)]
    tool_call_id: Annotated[str, Field(min_length=1, max_length=200)]
    invocation_id: Annotated[str, Field(min_length=1, max_length=160)]
    detail: Literal["low", "high", "original"] = "low"
    crop: tuple[int, int, int, int] | None = None
    video_audio: Literal["include", "visual_only"] = "include"

    @model_validator(mode="after")
    def crop_bounds(self):
        if self.crop and (
            min(self.crop) < 0 or self.crop[0] >= self.crop[2] or self.crop[1] >= self.crop[3]
        ):
            raise ValueError("Crop must have positive width and height")
        return self


def assert_reference_payload(value: object) -> None:
    """Fail closed before *any* persistent write, including LangGraph pending writes.

    This is a structured binary check, not a ban on ordinary prose about base64.
    Callers retain managed locators and immutable checksums for reconstruction.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        _leak()
    if isinstance(value, str) and INLINE_MEDIA.search(value):
        _leak()
    if isinstance(value, dict):
        if value.get("kind") == "binary" or value.get("type") == "input_audio":
            _leak()
        source = value.get("source")
        if isinstance(source, dict) and source.get("type") == "base64":
            _leak()
        block_type = value.get("type")
        if (
            isinstance(block_type, str)
            and block_type in {"image", "image_url", "input_image"}
            and any(key in value for key in ("data", "base64"))
        ):
            _leak()
        for child in value.values():
            assert_reference_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_reference_payload(child)


def _leak() -> None:
    logger.error("agent_media_entity_persistence_blocked", extra={"leak_count": 1})
    raise WorkflowError("agent_media_entity_leak", "媒体实体不能写入 Agent 持久状态", 500)


def reference_json(value: object, *, limit: int = 65536) -> str:
    assert_reference_payload(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limit:
        raise WorkflowError("agent_checkpoint_too_large", "Agent 轻状态超过存储上限", 500)
    return encoded
