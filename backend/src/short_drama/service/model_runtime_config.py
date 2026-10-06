"""宿主模型协议与加密扩展鉴权；能力缓存始终由可信已保存字段派生。"""

import json
from copy import deepcopy

from pydantic import ValidationError

from short_drama.ai.adapters import capability_fingerprint
from short_drama.ai.canvas_video_adapters import VIDEO_ADAPTERS
from short_drama.ai.model_identity import model_credential_identity
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import ConfigurationError
from short_drama.schemas.canvas_model_catalog import (
    MAX_SECRET_BYTES,
    CanvasChannelHeader,
    validate_channel_headers,
)
from short_drama.schemas.model_runtime_profile import ModelRuntimeProfile

PROTOCOL_ADAPTERS = {
    "chat-completion": "openai_chat.v1",
    "openai-response": "openai_responses.v1",
    "openai-image": "openai_images.v1",
    "volcengine-ark-image": "ark_images.v1",
    "qwen-image": "dashscope_images.v1",
    "dashscope-qwen-image": "dashscope_images.v1",
    "openai-audio": "openai_speech.v1",
    "volcengine-ark-video": "ark_video.v1",
    "qwen-video": "dashscope_video.v1",
    "dashscope-wan-video": "dashscope_video.v1",
    "newapi": "canvas_openai_videos.v1",
    "openai-video": "canvas_openai_videos.v1",
    "openai-videos": "canvas_openai_videos.v1",
    "newapi-channel-2": "canvas_newapi_video_generations.v1",
}


def legacy_runtime_profile(channel: dict, profile: dict) -> dict:
    values = {
        "version": 1,
        "api_format": channel.get("apiFormat", "openai"),
        "protocol": profile.get("protocol") or channel.get("interfaceType") or "unconfigured",
    }
    for key, value in (
        ("reference_asset_origin", channel.get("referenceAssetOrigin")),
        ("concurrency_limit", channel.get("concurrencyLimit")),
        ("capability_config", profile.get("capabilityConfig")),
        ("default_options", profile.get("defaultOptions")),
        ("logical_capability_spec", profile.get("logicalCapabilitySpec")),
        ("logical_capability_profiles", profile.get("logicalCapabilityProfiles")),
        ("video_capabilities_version", profile.get("videoCapabilitiesVersion")),
    ):
        if value is not None:
            values[key] = deepcopy(value)
    return ModelRuntimeProfile.model_validate(values).model_dump(mode="json", exclude_none=True)


def encrypt_runtime_credentials(secret_key: str, headers: dict[str, str], cipher) -> str | None:
    if not secret_key and not headers:
        return None
    if len(secret_key.encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError("runtime secret key exceeds byte limit")
    values = [CanvasChannelHeader(name=name, value=value) for name, value in headers.items()]
    validate_channel_headers(values)
    normalized = {value.name: value.value.get_secret_value() for value in values}
    return cipher.encrypt(json.dumps({"secretKey": secret_key, "headers": normalized}))


def decrypt_runtime_credentials(config, cipher) -> dict:
    encrypted = getattr(config, "runtime_credentials_cipher", None)
    if encrypted is None:
        return {"secretKey": "", "headers": {}}
    try:
        value = json.loads(cipher.decrypt(encrypted))
        if not isinstance(value, dict) or set(value) != {"secretKey", "headers"}:
            raise ValueError
        if not isinstance(value["secretKey"], str) or not isinstance(value["headers"], dict):
            raise ValueError
        headers = [
            CanvasChannelHeader(name=name, value=item) for name, item in value["headers"].items()
        ]
        validate_channel_headers(headers)
        if len(value["secretKey"].encode("utf-8")) > MAX_SECRET_BYTES:
            raise ValueError
        return {
            "secretKey": value["secretKey"],
            "headers": {item.name: item.value.get_secret_value() for item in headers},
        }
    except (ValueError, TypeError, ValidationError):
        raise ConfigurationError("模型扩展凭据无法解密或格式无效，请检查配置") from None


def model_runtime_public(config, cipher, binding=None) -> dict:
    values = decrypt_runtime_credentials(config, cipher)
    return {
        "runtime_profile": deepcopy(getattr(config, "runtime_profile", None)),
        "has_secret_key": bool(values["secretKey"]),
        "headers": [
            {"name": name, "has_value": bool(value)} for name, value in values["headers"].items()
        ],
        "credential_source": "beefapi"
        if binding is not None and binding.channel_key == "beefapi"
        else "manual",
    }


def runtime_protocol_adapter(profile: dict, channel_key: str, model_key: str, kind: str):
    if channel_key == "beefapi" and kind == "video":
        from short_drama.ai.canvas_video_adapters import is_seedance_model

        if is_seedance_model(model_key):
            return "canvas_beefapi_seedance_video.v1"
    return PROTOCOL_ADAPTERS.get(profile.get("protocol"))


def refresh_runtime_model(config, channel_key: str = "") -> bool:
    raw = getattr(config, "runtime_profile", None)
    if raw is None:
        return False
    try:
        profile = ModelRuntimeProfile.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError:
        raise GenerationError("unsupported_parameters") from None
    adapter = runtime_protocol_adapter(profile, channel_key, config.model_key, config.service_type)
    from short_drama.ai.adapters import ADAPTER_TYPES

    if profile["api_format"] != "openai" or ADAPTER_TYPES.get(adapter) != config.service_type:
        raise GenerationError("unsupported_protocol")
    identity = model_credential_identity(config)
    snapshot = {key: getattr(config, key) for key in ("base_url", "model_key", "service_type")}
    cache = {"adapter": adapter, "fingerprint": capability_fingerprint(snapshot, identity)}
    capability = profile.get("capability_config") or {}
    if adapter in VIDEO_ADAPTERS:
        cache["canvas_channel_key"] = channel_key
        if isinstance(capability.get("video"), dict):
            cache["canvas_video_capability"] = deepcopy(capability["video"])
        variants = (profile.get("default_options") or {}).get("variants")
        if variants is not None:
            cache["canvas_video_variants"] = deepcopy(variants)
    if config.service_type == "text" and isinstance(capability.get("text"), dict):
        cache["canvas_text_capability"] = deepcopy(capability["text"])
    if config.capability_cache == cache:
        return False
    config.capability_cache = cache
    return True
