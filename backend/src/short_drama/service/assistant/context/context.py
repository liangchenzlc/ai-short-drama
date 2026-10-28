"""Freeze saved work; a page reference never grants additional read or write access."""

import re

from short_drama.agent.context.media import MAX_DURATION_MS, MAX_INPUT_BYTES
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.service.canvas.document.document import resource_identifier


def _freeze_node_media(dao, project_id, row):
    """Only explicitly referenced primary media become managed multimodal inputs."""
    if row.kind not in {"image", "video", "audio"}:
        return None
    metadata = row.content_json.get("metadata", {})
    candidates = [metadata.get("storageKey"), metadata.get("content")]
    identifiers = {
        identifier for value in candidates if (identifier := resource_identifier(value)) is not None
    }
    if not identifiers:
        # An empty generator node is still useful as text context.
        if not any(candidates):
            return None
        raise WorkflowError(
            "assistant_node_media_unavailable",
            "引用节点尚未保存为可读取的媒体，请先保存或上传",
            422,
        )
    if len(identifiers) != 1:
        raise WorkflowError(
            "assistant_node_media_unavailable", "引用节点的媒体身份不一致，请重新读取画布", 422
        )
    media = dao.media(identifiers.pop())
    if media is None or media.project_id != project_id:
        raise NotFound("Selected node media does not belong to this project")
    if media.format_code.split("/")[0] != row.kind:
        raise WorkflowError("assistant_node_media_unavailable", "引用节点的媒体类型不匹配", 422)
    if not media.byte_size or media.byte_size > MAX_INPUT_BYTES[row.kind]:
        raise WorkflowError("assistant_node_media_unavailable", "引用媒体超过附件大小限制", 422)
    if not media.checksum_sha256 or not re.fullmatch(r"[0-9a-f]{64}", media.checksum_sha256):
        raise WorkflowError(
            "assistant_node_media_unavailable", "引用媒体缺少有效文件校验值，请重新上传", 422
        )
    if row.kind in {"video", "audio"} and (
        not media.duration_ms or media.duration_ms > MAX_DURATION_MS
    ):
        raise WorkflowError(
            "assistant_node_media_unavailable", "引用音视频须有实际时长且不能超过 120 秒", 422
        )
    return {
        "id": f"node:{row.node_key}",
        "kind": row.kind,
        "name": media.original_name or row.node_key,
        "text_content": None,
        "storage_locator": media.storage_locator,
        "checksum_sha256": media.checksum_sha256,
        "media_id": str(media.id),
    }


def _asset(row):
    return {
        "kind": "asset",
        "id": str(row.id),
        "revision": str(row.row_version),
        "name": row.name,
        "description": row.description,
        "prompt": row.prompt,
    }


def _shot(row):
    return {
        "kind": "shot",
        "id": str(row.id),
        "revision": str(row.row_version),
        "position": row.position,
        "script": row.script,
        "duration_ms": row.duration_ms,
        "source_excerpt": row.source_excerpt,
        "video_prompt": row.video_prompt,
    }
