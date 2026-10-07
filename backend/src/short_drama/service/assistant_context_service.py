"""Freeze saved work; a page reference never grants additional read or write access."""

import re
from copy import deepcopy

from short_drama.agent.input_media import MAX_DURATION_MS, MAX_INPUT_BYTES
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.assistant_context_dao import AssistantContextDAO
from short_drama.schemas.base import parse_identifier
from short_drama.service.canvas_document import resource_identifier

IDENTITY_FIELDS = frozenset(
    {"id", "kind", "type", "revision", "stage", "storageKey", "mimeType", "aspect"}
)


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


def _revision(actual, expected):
    if expected is not None and actual != expected:
        raise WorkflowError("assistant_context_changed", "作品版本已变化，请重新读取后发送", 409)


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


def _bounded(snapshot):
    """Bound provider context and record every text truncation for truthful display."""
    remaining = 96 * 1024
    truncated = list(snapshot.pop("truncated", []))

    def visit(value, path, field=""):
        nonlocal remaining
        if isinstance(value, str):
            if len(value) <= 700 and (
                field in IDENTITY_FIELDS or field.endswith(("_id", "_revision", "Id", "Ids"))
            ):
                return value
            data = value.encode("utf-8")
            allowed = min(remaining, 24 * 1024)
            remaining -= min(len(data), allowed)
            if len(data) <= allowed:
                return value
            truncated.append(path)
            return data[:allowed].decode("utf-8", errors="ignore")
        if isinstance(value, list):
            return [visit(item, f"{path}[{index}]", field) for index, item in enumerate(value)]
        if isinstance(value, dict):
            return {key: visit(item, f"{path}.{key}", key) for key, item in value.items()}
        return value

    bounded = visit(snapshot, "source")
    bounded["truncated"] = truncated
    return bounded


def freeze_assistant_context(session, project_id, context):
    if context is None:
        return None, [], []
    dao = AssistantContextDAO(session)
    project = dao.project(project_id)
    if project is None:
        raise NotFound("Project does not exist")
    snapshot = {
        "kind": context.kind,
        "id": context.id,
        "revision": str(context.revision),
        "project": {"id": str(project.id), "name": project.name},
        "selected": [],
        "truncated": [],
    }
    frozen_media = []
    if context.kind == "episode":
        episode = dao.episode(project_id, parse_identifier(context.id))
        if episode is None:
            raise NotFound("Episode does not belong to this project")
        _revision(episode.content_version, context.revision)
        _revision(episode.storyboard_version, context.storyboard_revision)
        snapshot.update(
            title=episode.title,
            stage=context.stage,
            storyboard_revision=str(episode.storyboard_version),
        )
        if context.include_document:
            novel, script = dao.writing(episode)
            snapshot.update(
                novel=novel.content if novel else "", script=script.content if script else ""
            )
        for selected in context.selected:
            row = (
                dao.asset(project_id, episode.id, parse_identifier(selected.id))
                if selected.kind == "asset"
                else dao.shot(episode.id, parse_identifier(selected.id))
            )
            if row is None:
                raise NotFound("Selected object does not belong to this episode")
            _revision(row.row_version, selected.revision)
            snapshot["selected"].append(_asset(row) if selected.kind == "asset" else _shot(row))
        if context.include_document:
            assets, shots = dao.assets(episode.id), dao.shots(episode.id)
            snapshot["assets"] = [_asset(row) for row in assets[:100]]
            snapshot["shots"] = [_shot(row) for row in shots[:100]]
            if len(assets) > 100:
                snapshot["truncated"].append("source.assets")
            if len(shots) > 100:
                snapshot["truncated"].append("source.shots")
    else:
        canvas = dao.canvas(project_id, context.id)
        if canvas is None:
            raise NotFound("Canvas does not belong to this project")
        _revision(canvas.row_version, context.revision)
        rows = dao.canvas_nodes(canvas.id)
        nodes = {row.node_key: row for row in rows}

        def node_data(row):
            content = deepcopy(row.content_json)
            # Only shared works enter auto context; model inputs and private histories do not.
            return {
                "kind": "node",
                "id": row.node_key,
                "revision": str(row.row_version),
                "type": row.kind,
                "content": content,
            }

        snapshot["title"] = canvas.title
        for selected in context.selected:
            row = nodes.get(selected.id)
            if row is None:
                raise NotFound("Selected node does not belong to this canvas")
            _revision(row.row_version, selected.revision)
            snapshot["selected"].append(node_data(row))
            if media := _freeze_node_media(dao, project_id, row):
                frozen_media.append(media)
        if context.include_document:
            snapshot["nodes"] = [node_data(row) for row in rows[:200]]
            if len(rows) > 200:
                snapshot["truncated"].append("source.nodes")
    if context.include_document:
        snapshot["project"].update(
            synopsis=project.synopsis, style=project.style, aspect=project.aspect
        )
    snapshot = _bounded(snapshot)
    reference = {
        "type": "source",
        "kind": context.kind,
        "id": context.id,
        "name": snapshot["title"],
        "revision": str(context.revision),
        "stage": context.stage,
        "selected": context.model_dump(mode="json")["selected"],
        "include_document": context.include_document,
        "truncated": snapshot["truncated"],
        "media": [
            {
                key: value
                for key, value in media.items()
                if key in {"id", "kind", "name", "media_id"}
            }
            for media in frozen_media
        ],
    }
    return snapshot, [reference], frozen_media
