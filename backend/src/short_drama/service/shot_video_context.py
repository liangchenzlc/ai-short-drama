"""Video context is independent of media adoption and image-only settings."""

import hashlib
import json

DEFAULT_VIDEO_SETTINGS = {"resolution": "720p"}
_UNSET = object()


def video_context_hash(
    shot_context_hash,
    reference_media_id,
    prompt,
    settings,
    *,
    session=None,
    shot=None,
    native_speech=_UNSET,
):
    value = {
        "version": "shot-video-context-v2",
        "input_mode": "omni_reference",
        "shot_context_hash": shot_context_hash,
        "reference_media_id": str(reference_media_id) if reference_media_id else None,
        "video_prompt": prompt or "",
        "video_settings": {
            k: v for k, v in (settings or DEFAULT_VIDEO_SETTINGS).items() if v is not None
        },
    }
    if native_speech is _UNSET and session is not None and shot is not None:
        from .native_voice_service import native_context

        native_speech = native_context(session, shot)
    if native_speech is not _UNSET and native_speech is not None:
        value["native_speech"] = native_speech
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
