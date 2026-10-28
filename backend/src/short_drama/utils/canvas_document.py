"""Lossless author projection with an explicit shared-work metadata allowlist."""

import json
import re
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from urllib.parse import parse_qs, urlencode, urlsplit

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas.document.canvas import CanvasDocument
from short_drama.utils.canvas_media_locators import canonicalize_canvas_media

# New upstream fields remain private until their publication semantics are reviewed.
SHARED_METADATA = frozenset(
    """
nodeRole resultOrigin generatedFromNodeId content previewContent videoPreview richText locked
fontSize naturalWidth naturalHeight freeResize primaryImageId imageBatchExpanded storageKey
mimeType bytes durationMs hasAudio assetTags assetCategory workflowKind workflowTitle
workflowDescription chapterTitle shotIndex characterName characterAliases characterDefinition
characterCoverUrl characterView characterViewNodeIds videoMergeSourceNodeIds
depthSourceNodeId videoFrameSourceNodeId videoFrameTimeMs videoSegmentSourceNodeId
videoSegmentStartMs videoSegmentEndMs videoSegmentIndex videoSegmentAction videoTrimStartMs
videoTrimEndMs videoTrimSourceNodeId videoTrimSource videoCrop videoCropSourceNodeId
audioExtractSourceNodeId audioExtractStartMs audioExtractEndMs videoAudioSourceNodeId
videoHasAudio versionOfNodeId versionLabel versionPrimary copiedFromNodeId directorSceneId
directorShotId directorPreviewNodeId directorCoverStorageKey directorCoverUrl
directorCoverSceneUpdatedAt directorDepthNodeId directorNormalNodeId directorClayVideoNodeId
subtitleEntries subtitleHighlights subtitleStyle subtitleUpdatedAt chartKind colorGrade
manualSize storyboard frame folder drawingId drawingEngine drawingRevision drawingUpdatedAt
drawingPreviewStorageKey drawingPreviewUrl drawingShapeCount drawingPageCount
referenceAssetNodeIds assetBindings characterVoiceName characterVoiceProfile
    """.split()
)
SKILL_BODY_METADATA = frozenset({"content", "previewContent", "richText"})
PRIVATE_ROOT = frozenset(
    {
        "chatSessions",
        "activeChatId",
        "appearance",
        "backgroundMode",
        "showImageInfo",
        "viewport",
        "folderId",
    }
)
SERVER_ROOT = frozenset(
    {
        "id",
        "revision",
        "remoteContentHash",
        "workspaceProjectId",
        "projectId",
        "createdAt",
        "updatedAt",
    }
)
PRIVATE_NESTED = re.compile(
    r"^(task|configId$|modelConfig|provider|generation|error|failed|sessionId$|conversationId$|"
    r"messageId$|resultId$|retryOf$|attemptGroupId$|assetId$|prompt$|skill|request|response)",
    re.I,
)
RESOURCE_KEY = re.compile(r"^resource:([1-9][0-9]*)$")
RESOURCE_URL = re.compile(r"^/api/(?:v1/canvas-runtime/)?resources/([1-9][0-9]*)(?:/.*)?$")
RESOURCE_LOCATOR_FIELDS = frozenset(
    "storageKey content previewContent drawingPreviewStorageKey drawingPreviewUrl url dataUrl "
    "coverUrl characterCoverUrl imageUrl videoUrl audioUrl referenceUrl referenceUrls artifactRef "
    "providerArtifactRef".split()
)
RESOURCE_ID_FIELDS = frozenset(
    "resourceId resourceIds sampleResourceId referenceResourceId referenceResourceIds".split()
)
PROJECTION_KEY = "$canvas_projection"
ABSENT = object()
CHARACTER_UNVERIFIED_FIELDS = frozenset({"characterVoiceName", "characterVoiceProfile"})
CHARACTER_PRIVATE_NESTED = re.compile(
    r"^(api_?key$|secret|credential|authorization$|access_?token$|model$|modelId$)", re.I
)


def character_node(node: dict) -> bool:
    metadata = node.get("metadata") or {}
    return metadata.get("workflowKind") == "character" or "characterCoverUrl" in metadata


def has_character_nodes(document: CanvasDocument | dict | None) -> bool:
    if document is None:
        return False
    if isinstance(document, CanvasDocument):
        document = document.model_dump(mode="json", by_alias=True, exclude_unset=True)
    return any(character_node(node) for node in document.get("nodes", []))


def content_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _item_identity(value) -> str:
    if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
        return "id:" + value["id"]
    # Old/imported structures without an id can only be matched while their public
    # content remains identical. Never infer identity from an array position.
    return "content:" + content_hash(value)


def _split_nested(value, extra_private=None):
    if isinstance(value, dict):
        shared, private = {}, {}
        for key, item in value.items():
            if PRIVATE_NESTED.match(key) or extra_private and extra_private.match(key):
                private[key] = deepcopy(item)
            else:
                public, hidden = _split_nested(item, extra_private)
                shared[key] = public
                if hidden is not None:
                    private[key] = hidden
        return shared, {PROJECTION_KEY: {"kind": "object", "fields": private}} if private else None
    if isinstance(value, list):
        pairs = [_split_nested(item, extra_private) for item in value]
        shared = [public for public, _ in pairs]
        identities = [_item_identity(item) for item in shared]
        counts = Counter(identities)
        entries = {}
        for identity, (_, hidden) in zip(identities, pairs, strict=True):
            if hidden is None:
                continue
            if counts[identity] != 1:
                raise WorkflowError(
                    "canvas_private_identity_required",
                    "含私人参数的重复条目缺少唯一稳定 ID；请修复条目身份后再保存",
                    422,
                )
            entries[identity] = hidden
        return shared, {PROJECTION_KEY: {"kind": "array", "entries": entries}} if entries else None
    return deepcopy(value), None


def overlay(shared, private):
    if isinstance(private, dict) and PROJECTION_KEY in private:
        projection = private[PROJECTION_KEY]
        if projection["kind"] == "object":
            return overlay(shared, projection["fields"]) if isinstance(shared, dict) else shared
        if not isinstance(shared, list):
            return shared
        identities = [_item_identity(item) for item in shared]
        counts = Counter(identities)
        entries = projection["entries"]
        return [
            overlay(item, entries[identity])
            if counts[identity] == 1 and identity in entries
            else deepcopy(item)
            for identity, item in zip(identities, shared, strict=True)
        ]
    if isinstance(shared, dict) and isinstance(private, dict):
        result = deepcopy(shared)
        for key, value in private.items():
            projected = overlay(result.get(key, ABSENT), value)
            if projected is not ABSENT:
                result[key] = projected
        return result
    if isinstance(shared, list) and isinstance(private, list):
        raise WorkflowError(
            "canvas_private_projection_upgrade_required",
            "旧私人数组缺少稳定身份，已保留原数据；请先显式迁移，不能按位置自动对应",
            409,
        )
    return deepcopy(private)


def _split_conversion_work(value):
    """仅发布转换作品及来源，处理状态、诊断和未知字段由作者投影恢复。"""
    if not isinstance(value, dict):
        return {}, deepcopy(value)
    allowed = {
        "schemaVersion": lambda item: type(item) is int and item == 1,
        "operation": lambda item: (
            isinstance(item, str)
            and item in {"grayscale", "edge-canny", "lineart", "depth", "pose", "cutout"}
        ),
        "sourceNodeId": lambda item: isinstance(item, str) and 0 < len(item) <= 128,
        "sourceFingerprint": lambda item: isinstance(item, str) and 0 < len(item) <= 128,
        "outputKind": lambda item: item == "image",
        "resultStorageKey": lambda item: (
            isinstance(item, str)
            and bool(RESOURCE_KEY.fullmatch(item))
            and len(item) <= 29
            and int(item[9:]) <= 2**64 - 1
        ),
        "resultWidth": lambda item: type(item) is int and 0 < item <= 32768,
        "resultHeight": lambda item: type(item) is int and 0 < item <= 32768,
        "detectedPeople": lambda item: type(item) is int and item >= 0,
    }
    public = {
        key: deepcopy(item) for key, item in value.items() if key in allowed and allowed[key](item)
    }
    hidden = {key: deepcopy(item) for key, item in value.items() if key not in public}
    if "status" in value:
        public["status"] = (
            value["status"]
            if isinstance(value["status"], str) and value["status"] in {"completed", "stale"}
            else "stale"
            if public.get("resultStorageKey")
            else "idle"
        )
        if value["status"] == public["status"]:
            hidden.pop("status", None)
    return public, {PROJECTION_KEY: {"kind": "object", "fields": hidden}}


def split_document(document: CanvasDocument, *, strict_media=True) -> tuple[dict, dict]:
    source = canonicalize_canvas_media(
        document.model_dump(mode="json", by_alias=True, exclude_unset=True), strict=strict_media
    )
    shared = {
        key: deepcopy(value)
        for key, value in source.items()
        if key not in PRIVATE_ROOT | SERVER_ROOT
    }
    private = {
        "root": {key: deepcopy(value) for key, value in source.items() if key in PRIVATE_ROOT},
        "nodes": {},
    }
    for key in ("timeline", "directorScenes"):
        if key in shared:
            shared[key], hidden = _split_nested(shared[key])
            if hidden is not None:
                private["root"][key] = hidden
    for node in shared.get("nodes", []):
        metadata = node.get("metadata", {})
        public, hidden = {}, {}
        for key, value in metadata.items():
            if key == "mediaConversion" and node.get("type") == "media-conversion":
                public[key], hidden[key] = _split_conversion_work(value)
            elif key in SHARED_METADATA and not (
                node.get("type") == "skill"
                and key in SKILL_BODY_METADATA
                or character_node(node)
                and key in CHARACTER_UNVERIFIED_FIELDS
            ):
                public[key], nested = _split_nested(
                    value,
                    CHARACTER_PRIVATE_NESTED
                    if character_node(node) and key == "characterDefinition"
                    else None,
                )
                if nested is not None:
                    hidden[key] = nested
            else:
                hidden[key] = deepcopy(value)
        if "metadata" in node:
            node["metadata"] = public
        private["nodes"][node["id"]] = hidden
    return shared, private


def project_document(shared: dict, private: dict | None) -> dict:
    result = deepcopy(shared)
    # Old rows have no reliable body-author identity. Never expose or assign
    # their shared Skill prose; only a trusted personal projection may restore it.
    for node in result.get("nodes", []):
        if node.get("type") == "skill":
            for field in SKILL_BODY_METADATA:
                node.get("metadata", {}).pop(field, None)
    if private:
        result = overlay(result, private.get("root", {}))
        for node in result.get("nodes", []):
            metadata = private.get("nodes", {}).get(node["id"])
            if metadata:
                node["metadata"] = overlay(node.get("metadata", {}), metadata)
    return result


def resource_identifier(value: object, *, bare: bool = False) -> int | None:
    """Resolve source/host resource locators without fetching an external URL."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if bare and re.fullmatch(r"[1-9][0-9]*", value):
        identifier = value
    elif match := RESOURCE_KEY.fullmatch(value):
        identifier = match[1]
    else:
        try:
            url = urlsplit(value)
        except ValueError:
            return None
        if url.scheme not in {"", "http", "https"} or not (
            match := RESOURCE_URL.fullmatch(url.path)
        ):
            return None
        identifier = match[1]
    if len(identifier) > 20 or int(identifier) > 2**64 - 1:
        raise WorkflowError("canvas_resource_invalid", "资源 ID 超出有效范围", 422)
    return int(identifier)


def media_references(value, path=()):
    """Enumerate stable identities, including the source's URL and bare-ID fields."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from media_references(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from media_references(item, (*path, str(index)))
    elif isinstance(value, str):
        field = next((part for part in reversed(path) if not part.isdecimal()), "")
        if (
            value.strip().startswith("resource:")
            or field in RESOURCE_LOCATOR_FIELDS | RESOURCE_ID_FIELDS
        ) and (identifier := resource_identifier(value, bare=field in RESOURCE_ID_FIELDS)):
            yield path, identifier


def remap_resource_references(document: dict, mapping: dict[int, int]) -> dict:
    """Canonicalize only recognized media fields, retaining prose and task identities."""
    result = deepcopy(document)
    for path, identifier in media_references(document):
        target = mapping.get(identifier)
        if target is None or target == identifier:
            continue
        parent = result
        for part in path[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        field = next((part for part in reversed(path) if not part.isdecimal()), "")
        original = parent[int(path[-1]) if isinstance(parent, list) else path[-1]].strip()
        if field in RESOURCE_ID_FIELDS and original.isdecimal():
            value = str(target)
        elif RESOURCE_KEY.fullmatch(original):
            value = f"resource:{target}"
        else:
            url = urlsplit(original)
            suffix = RESOURCE_URL.fullmatch(url.path)
            tail = url.path[suffix.end(1) :]
            query = parse_qs(url.query)
            retained = {
                key: allowed
                for key, allowed in (("variant", "playback"), ("proxy", "1"))
                if query.get(key, [None])[0] == allowed
            }
            value = f"/api/v1/canvas-runtime/resources/{target}{tail}"
            if retained:
                value += "?" + urlencode(retained)
            if url.fragment:
                value += "#" + url.fragment
        parent[int(path[-1]) if isinstance(parent, list) else path[-1]] = value
    return result
