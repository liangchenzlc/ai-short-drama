"""画布专用内存凭据；公开请求和配置快照只携带闭合版本标识。"""

import json
from typing import Literal

from pydantic import ConfigDict, Field, SecretStr, ValidationError, model_validator

from short_drama.schemas.base import InputModel
from short_drama.schemas.canvas_model_catalog import (
    MAX_CHANNEL_HEADERS,
    MAX_SECRET_BYTES,
    CanvasChannelHeader,
    validate_channel_headers,
)

from .canvas_runninghub_adapters import RUNNINGHUB_ADAPTERS
from .canvas_video_adapters import VIDEO_ADAPTERS
from .types import GenerationError

CANVAS_AUTH_SCENES = frozenset({"canvas_node", "canvas_model_test", "canvas_tool"})
CANVAS_AUTH_ADAPTERS = frozenset(
    {
        *VIDEO_ADAPTERS,
        *RUNNINGHUB_ADAPTERS,
        "openai_chat.v1",
        "openai_responses.v1",
        "claude_messages.v1",
        "gemini_generate_content.v1",
        "openai_images.v1",
        "openai_speech.v1",
        "canvas_async_audio.v1",
        "ark_images.v1",
        "ark_video.v1",
        "dashscope_images.v1",
        "dashscope_video.v1",
        "modelhub_video.v1",
    }
)
MODEL_AUTH_ADAPTERS = CANVAS_AUTH_ADAPTERS | frozenset(
    {"dashscope_speech.v1", "modelhub_video.v1", "dashscope_voice_design.v1"}
)


class CanvasCredentials(InputModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    version: Literal[1] = 1
    api_key: SecretStr = Field(alias="apiKey")
    headers: list[CanvasChannelHeader] = Field(default_factory=list, max_length=MAX_CHANNEL_HEADERS)
    runninghub_upload_key: SecretStr | None = Field(default=None, alias="runningHubUploadApiKey")

    @model_validator(mode="before")
    @classmethod
    def exact_version(cls, value):
        if isinstance(value, dict) and "version" in value and type(value["version"]) is not int:
            raise ValueError("invalid canvas credential version")
        return value

    @model_validator(mode="after")
    def safe_credentials(self):
        self.api_key = SecretStr(self.api_key.get_secret_value().strip())
        if self.runninghub_upload_key is not None:
            self.runninghub_upload_key = SecretStr(
                self.runninghub_upload_key.get_secret_value().strip()
            )
        for value in (
            self.api_key.get_secret_value(),
            self.runninghub_upload_key.get_secret_value() if self.runninghub_upload_key else "",
        ):
            if len(value.encode("utf-8")) > MAX_SECRET_BYTES or any(
                ord(char) < 33 or ord(char) > 126 for char in value
            ):
                raise ValueError("invalid canvas credential")
        validate_channel_headers(self.headers)
        return self


def canvas_auth_enabled(snapshot: dict) -> bool:
    return (
        type(snapshot.get("canvas_auth_version")) is int
        and snapshot.get("canvas_auth_version") == 1
        and snapshot.get("canvas_auth_scene") in CANVAS_AUTH_SCENES
    )


def model_auth_enabled(snapshot: dict) -> bool:
    return (
        type(snapshot.get("model_auth_version")) is int
        and snapshot.get("model_auth_version") == 1
        and snapshot.get("model_auth_scope") == "generation"
        and snapshot.get("model_auth_kind") == snapshot.get("service_type")
        and snapshot.get("model_auth_kind") in {"text", "image", "video", "audio"}
        and isinstance(snapshot.get("model_auth_scene"), str)
        and snapshot.get("model_auth_scene") not in CANVAS_AUTH_SCENES
    )


def decode_canvas_credentials(
    snapshot: dict, request: dict, plaintext: str
) -> str | CanvasCredentials:
    if "canvas_auth_version" not in snapshot and "model_auth_version" not in snapshot:
        return plaintext
    scene = (request.get("source") or {}).get("scene")
    valid_canvas = canvas_auth_enabled(snapshot) and snapshot.get("canvas_auth_scene") == scene
    valid_model = model_auth_enabled(snapshot) and snapshot.get("model_auth_scene") == (scene or "")
    if "canvas_auth_version" in snapshot and "model_auth_version" in snapshot:
        raise GenerationError("invalid_credential")
    if not valid_canvas and not valid_model:
        raise GenerationError("invalid_credential")
    try:
        return CanvasCredentials.model_validate(json.loads(plaintext))
    except (ValueError, TypeError, ValidationError):
        raise GenerationError("invalid_credential") from None


def canvas_authentication(
    snapshot: dict, credential, headers: dict, *, adapter=None
) -> tuple[str, dict, list[str]]:
    if not isinstance(credential, CanvasCredentials) or not (
        canvas_auth_enabled(snapshot) or model_auth_enabled(snapshot)
    ):
        raise GenerationError("invalid_credential")
    try:
        value = CanvasCredentials.model_validate(credential.model_dump())
    except (ValueError, ValidationError):
        raise GenerationError("invalid_credential") from None
    result = dict(headers)
    claude = adapter == "claude_messages.v1"
    gemini = adapter == "gemini_generate_content.v1"
    runninghub = adapter in RUNNINGHUB_ADAPTERS
    if (claude or gemini) and not canvas_auth_enabled(snapshot):
        raise GenerationError("invalid_credential")
    secrets = []
    for item in value.headers:
        secret = item.value.get_secret_value()
        # Go's net/http writes UTF-8 header bytes; Python's str path uses Latin-1.
        name = item.name.lower() if claude else item.name
        if claude:
            result = {key: value for key, value in result.items() if key.lower() != name}
        result[name] = secret.encode("utf-8") if not secret.isascii() else secret
        if secret:
            secrets.append(secret)
    key = value.api_key.get_secret_value()
    if runninghub:
        if not canvas_auth_enabled(snapshot) or not key:
            raise GenerationError("invalid_credential")
        upload_key = (
            value.runninghub_upload_key.get_secret_value() if value.runninghub_upload_key else ""
        )
        secrets.extend(item for item in (key, upload_key) if item)
        return key, result, sorted(set(secrets), key=len, reverse=True)
    if gemini and not key:
        raise GenerationError("invalid_credential")
    if key:
        if claude or gemini:
            auth_header = "x-api-key" if claude else "x-goog-api-key"
            result = {
                name: value
                for name, value in result.items()
                if name.lower() not in {auth_header, "authorization"}
            }
            result[auth_header] = key
        else:
            result["Authorization"] = "Bearer " + key
        secrets.append(key)
    if claude and not any(name.lower() == "anthropic-version" for name in result):
        result["anthropic-version"] = "2023-06-01"
    return key, result, sorted(set(secrets), key=len, reverse=True)
