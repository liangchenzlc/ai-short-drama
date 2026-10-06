"""固定源 BeefAPI catalog.go 的模型能力、协议及公开目录合并规则。"""

import re
from copy import deepcopy

from .canvas_catalog_payload import normalize_video_capabilities


def catalog_profile(model: dict) -> dict:
    identifier = model["id"].strip().lower()
    kind = str(model.get("modelType", "")).strip().lower()
    endpoints = {value.strip().lower() for value in model.get("supportedEndpointTypes", [])}
    tokens = set(re.split(r"[\W_]+", identifier))
    transcription = bool(
        tokens & {"asr", "stt", "whisper", "transcription", "transcriptions", "transcribe"}
    )
    speech = bool(tokens & {"speech", "tts", "music"}) and not transcription
    capability = kind if kind in {"text", "image", "video", "audio"} else ""
    protocol = ""
    if any(
        identifier == name or identifier.startswith(name + "-")
        for name in ("seedance-2.0", "seedance-2.5")
    ):
        capability, protocol = "video", "newapi"
    elif identifier == "wan3.0-video":
        capability, protocol = "video", "newapi-channel-2"
    elif endpoints & {"audio.transcriptions", "audio-transcriptions", "transcriptions"} or (
        kind in {"", "audio"} and transcription
    ):
        capability = ""
    else:
        for mapped_kind, mapped_protocol, names in (
            ("image", "openai-image", {"image-generation", "images", "images.generations"}),
            ("video", "openai-videos", {"openai-video", "videos", "videos.generations"}),
            ("audio", "openai-audio", {"audio.speech", "audio-speech"}),
            (
                "text",
                "openai-response",
                {"openai-response", "openai-response-compact", "responses"},
            ),
            ("text", "claude-api", {"anthropic", "messages"}),
            ("text", "gemini-generate-content", {"gemini"}),
        ):
            if endpoints & names:
                capability = capability or mapped_kind
                if not protocol and capability == mapped_kind:
                    protocol = mapped_protocol
        if not capability and not protocol and speech:
            capability, protocol = "audio", "openai-audio"
        if capability == "audio" and not protocol and (speech or kind == "audio"):
            protocol = "openai-audio"
        if endpoints & {"openai", "chat.completions"}:
            capability = capability or "text"
            if not protocol and capability == "text":
                protocol = "chat-completion"
        if not protocol:
            protocol = {
                "text": "chat-completion",
                "image": "openai-image",
                "video": "openai-videos",
                "audio": "openai-audio",
            }.get(capability, "")
    profile = {"model": model["id"], "capability": capability, "protocol": protocol}
    if model.get("displayName"):
        profile["displayName"] = model["displayName"]
    if capability == "video" and (
        video := normalize_video_capabilities(model.get("videoCapabilities"))
    ):
        profile["capabilityConfig"] = {"version": 1, "video": video}
        profile["videoCapabilitiesVersion"] = model.get("videoCapabilitiesVersion", "")
    return profile


def merge_catalog(channel: dict, models: list[dict], *, replace: bool) -> dict:
    result = deepcopy(channel)
    profiles = [] if replace else deepcopy(result.get("modelProfiles", []))
    by_model = {item["model"]: item for item in profiles}
    for model in models:
        incoming = catalog_profile(model)
        if incoming["model"] not in by_model:
            profiles.append(incoming)
            by_model[incoming["model"]] = incoming
        else:
            by_model[incoming["model"]].update(incoming)
    result["modelProfiles"] = profiles
    result["models"] = list(
        dict.fromkeys(
            ([] if replace else result.get("models", [])) + [item["id"] for item in models]
        )
    )
    return result
