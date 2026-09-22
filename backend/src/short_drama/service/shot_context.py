"""Canonical creative context used for shot image generation and adoption."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from short_drama.schemas.base import parse_identifier

SHOT_CONTEXT_VERSION = "shot-context-v1"


def _value(asset: Mapping[str, Any] | object, name: str, default=None):
    if isinstance(asset, Mapping):
        return asset.get(name, default)
    return getattr(asset, name, default)


def normalize_shot_context(
    *,
    shot_id: int,
    script: str,
    duration_ms: int,
    episode_aspect: str,
    episode_style: str,
    assets: Iterable[Mapping[str, Any] | object],
) -> dict[str, Any]:
    """Return the versioned, URL-free representation described by shot-context-v1."""

    normalized_assets = []
    for asset in assets:
        identifier = parse_identifier(_value(asset, "id"))
        media_id = _value(asset, "media_id")
        normalized_assets.append(
            {
                "id": str(identifier),
                "kind": _value(asset, "kind"),
                "name": _value(asset, "name"),
                "label": _value(asset, "label", "") or "",
                "description": _value(asset, "description", "") or "",
                "prompt": _value(asset, "prompt", "") or "",
                "tags": sorted(_value(asset, "tags", []) or []),
                "scene_time": _value(asset, "scene_time", "") or "",
                "state": _value(asset, "state", "unconfirmed") or "unconfirmed",
                "media_id": str(parse_identifier(media_id)) if media_id is not None else None,
            }
        )
    normalized_assets.sort(key=lambda item: int(item["id"]))
    return {
        "version": SHOT_CONTEXT_VERSION,
        "shot_id": str(parse_identifier(shot_id)),
        "shot": {"script": script, "duration_ms": duration_ms},
        "episode": {"aspect": episode_aspect, "style": episode_style},
        "assets": normalized_assets,
    }


def compute_shot_context_hash(**values) -> str:
    context = normalize_shot_context(**values)
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
