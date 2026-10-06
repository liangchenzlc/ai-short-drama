"""Port of BeefTV asset/library_canvas_guard.go for source media bindings."""

from collections.abc import Iterator

from short_drama.core.exceptions import WorkflowError

from .canvas_document import resource_identifier


def _object(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkflowError(
            "canvas_asset_reference_invalid", "已有画布媒体无法解析，已停止修改素材", 409
        )
    return value


def _string(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise WorkflowError(
            "canvas_asset_reference_invalid", "已有画布媒体无法解析，已停止修改素材", 409
        )
    return value.strip()


def _items(value) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WorkflowError(
            "canvas_asset_reference_invalid", "已有画布媒体无法解析，已停止修改素材", 409
        )
    return value


def canvas_media_bindings(document: dict) -> Iterator[tuple[str, int]]:
    """Use the source's first valid locator, and only its three media node kinds."""
    candidates = []
    for raw in _items(document.get("nodes")):
        node = _object(raw)
        candidates.append(
            (node.get("type"), _object(node.get("metadata")), ("storageKey", "content"))
        )
    for raw in _items(_object(document.get("timeline")).get("clips")):
        direct = _object(_object(raw).get("directMedia"))
        candidates.append((direct.get("kind"), direct, ("storageKey", "url", "dataUrl", "content")))
    for kind, media, fields in candidates:
        if _string(kind).lower() not in {"image", "video", "audio"}:
            continue
        for field in fields:
            identifier = resource_identifier(_string(media.get(field)))
            if identifier is not None:
                yield _string(media.get("assetId")), identifier
                break
