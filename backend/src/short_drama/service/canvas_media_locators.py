"""Canonical file locations for source media fields, without rewriting authored prose."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit

from short_drama.core.exceptions import WorkflowError

MEDIA_KINDS = {"image", "video", "audio", "model", "panorama"}
TEXT_KINDS = {
    "text",
    "markdown",
    "html",
    "svg",
    "script",
    "skill",
    "config",
    "batch-table",
    "chart",
}
DERIVED_PAIRS = {
    "drawingPreviewUrl": "drawingPreviewStorageKey",
    "directorCoverUrl": "directorCoverStorageKey",
}
RESOURCE_KEY = re.compile(r"^resource:([1-9][0-9]*)$")
RESOURCE_URL = re.compile(r"^/api/(?:v1/canvas-runtime/)?resources/([1-9][0-9]*)(/.*)?$")


def _resource(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    key = RESOURCE_KEY.fullmatch(text)
    url = match = None
    if not key and re.match(r"^(?:/|https?:)", text, re.I):
        try:
            url = urlsplit(text)
        except ValueError:
            return None
        match = RESOURCE_URL.fullmatch(url.path)
    if not (key or match):
        return None
    identifier = (key or match)[1]
    if len(identifier) > 20 or int(identifier) > 2**64 - 1:
        raise WorkflowError("canvas_resource_invalid", "资源 ID 超出有效范围", 422)
    return identifier, (match[2] if match else None) or "/file", url


def _file_url(found):
    identifier, suffix, url = found
    query = parse_qs(url.query) if url else {}
    retained = {
        key: allowed
        for key, allowed in (("variant", "playback"), ("proxy", "1"))
        if query.get(key, [None])[0] == allowed
    }
    result = f"/api/v1/canvas-runtime/resources/{identifier}{suffix}"
    if retained:
        result += "?" + urlencode(retained)
    if url and url.fragment:
        result += "#" + url.fragment
    return result


def _transient(value):
    return isinstance(value, str) and re.match(r"^(blob:|data:)", value.strip(), re.I)


def canonicalize_canvas_media(document, *, strict=True):
    def visit(value, inherited="", depth=0):
        if depth >= 64:
            raise WorkflowError("canvas_resource_invalid", "画布媒体结构无法保存", 422)
        if isinstance(value, list):
            return [visit(item, depth=depth + 1) for item in value]
        if not isinstance(value, dict):
            return value
        node_text = isinstance(value.get("type"), str) and value["type"] in TEXT_KINDS
        is_text = inherited == "text" or value.get("kind") == "text" or node_text
        media = not is_text and (
            inherited == "media"
            or (isinstance(value.get("kind"), str) and value["kind"] in MEDIA_KINDS)
            or re.match(r"^(image|video|audio)/", str(value.get("mimeType", "")), re.I)
        )
        node_media = isinstance(value.get("type"), str) and value["type"] in MEDIA_KINDS
        result = {}
        for key, item in value.items():
            context = ""
            if key == "metadata":
                context = "media" if node_media else "text" if node_text else ""
            elif key == "videoTrimSource":
                context = "media"
            result[key] = visit(item, context, depth + 1)

        def normalize(field, storage_field, *, derived=False):
            original = value.get(field)
            if not isinstance(original, str) or not original.strip():
                return
            own = _resource(original)
            stable = _resource(value.get(storage_field))
            if own:
                result[field] = _file_url(own)
            elif stable and (_transient(original) or re.match(r"^https?:", original.strip(), re.I)):
                result[field] = _file_url(stable)
            elif _transient(original):
                if derived:
                    result.pop(field, None)
                elif strict:
                    raise WorkflowError(
                        "canvas_media_not_persisted",
                        "画布媒体尚未持久化，原有草稿已保留，请完成上传后重试",
                        422,
                    )

        if media:
            for key in ("content", "url", "dataUrl"):
                normalize(key, "storageKey")
            if _transient(value.get("previewContent")):
                result.pop("previewContent", None)
        for field, storage_field in DERIVED_PAIRS.items():
            normalize(field, storage_field, derived=True)
        return result

    return visit(document)
