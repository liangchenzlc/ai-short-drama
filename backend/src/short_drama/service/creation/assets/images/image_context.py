import hashlib
import json


def asset_image_content(asset) -> dict:
    fields = ("kind", "name", "description", "prompt", "label", "tags", "scene_time")
    result = {field: getattr(asset, field) for field in fields}
    result["tags"] = sorted(set(result["tags"] or []))
    references = getattr(asset, "reference_media_ids", None) or []
    if references:
        result["reference_media_ids"] = list(map(str, references))
    return result


def asset_image_content_hash(content: dict) -> str:
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def asset_image_stale_reason(asset, request: dict) -> str | None:
    source = request.get("source") or {}
    if source.get("scene") != "asset_image":
        return None
    if str(source.get("asset_id")) != str(asset.id):
        return "source_mismatch"
    snapshot = request.get("source_snapshot") or {}
    if not snapshot.get("asset_content_hash"):
        return "snapshot_missing"
    if snapshot["asset_content_hash"] != asset_image_content_hash(asset_image_content(asset)):
        return "content_changed"
    return None
