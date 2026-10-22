"""V3 subject snapshots, independent from the legacy whole-document context protocol."""

import json
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import select

from short_drama.agent.authorization import digest
from short_drama.agent.subject_context import snapshot_subjects, subject_catalog, subject_key
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.assistant_context_dao import AssistantContextDAO
from short_drama.dao.image_group_dao import ImageGroupDAO
from short_drama.domain import ShotDialogue
from short_drama.domain.canvas import CanvasNodeUserState
from short_drama.schemas.assistant_task import TaskSubjectReference
from short_drama.service.assistant_context_service import _asset, _freeze_node_media, _shot
from short_drama.service.assistant_dialogue_service import sound_line_state
from short_drama.service.assistant_image_service import freeze_image_catalog
from short_drama.service.assistant_voice_service import voice_binding_state

NODE_FIELDS = frozenset(
    "content richText storageKey mimeType durationMs nodeRole characterName characterAliases "
    "characterDefinition workflowTitle workflowDescription".split()
)
MAX_SNAPSHOT_BYTES = 1024 * 1024


def _project(dao, owner, project_id, source_kind):
    project = dao.project(int(project_id))
    if (
        project is None
        or project.owner_user_id != owner
        or project.archived_at is not None
        or project.workspace_mode != ("infinite_canvas" if source_kind == "canvas" else "standard")
    ):
        raise NotFound("参考作品不存在或无权访问")
    return project


def _group_target(dao, project_id, group):
    target = (
        dao.asset(project_id, group.asset_id)
        if group.asset_id is not None
        else dao.shot(project_id, group.shot_id)
    )
    if target is None:
        raise NotFound("图片组的所属主体已删除或不再可访问")


def _slot_fields(row):
    return {
        "id": str(row.id),
        "output_index": row.output_index,
        "item_version": str(row.item_version),
        "media_id": str(row.media_id) if row.media_id else None,
    }


def _current(dao, owner, reference):
    """Read only the selected object; layout and unrelated objects are not content versions."""
    project = _project(dao, owner, reference.project_id, reference.source_kind)
    project_id = project.id
    content_revision, media_ids, write_versions, voice_binding = None, [], None, None
    sound_binding = None
    location = {
        "project_id": str(project_id),
        "kind": reference.kind,
        "id": reference.id,
        **({"canvas_id": reference.source_id} if reference.kind == "node" else {}),
    }
    if reference.kind == "node":
        canvas = dao.canvas(project_id, reference.source_id)
        row = (
            next((r for r in dao.canvas_nodes(canvas.id) if r.node_key == reference.id), None)
            if canvas
            else None
        )
        if row is None:
            raise NotFound("已选节点已删除或不再可访问")
        fields = {
            "type": row.kind,
            "title": row.content_json.get("title", ""),
            "metadata": {
                key: deepcopy(value)
                for key, value in row.content_json.get("metadata", {}).items()
                if key in NODE_FIELDS
            },
        }
        revision, content_revision = row.row_version, row.content_version
        private_revision = (
            dao.session.scalar(
                select(CanvasNodeUserState.row_version)
                .where(
                    CanvasNodeUserState.canvas_id == canvas.id,
                    CanvasNodeUserState.node_key == row.node_key,
                    CanvasNodeUserState.user_id == owner,
                )
                .with_for_update()
            )
            or 0
        )
        write_versions = {
            "row_version": str(revision),
            "private_revision": str(private_revision),
        }
        name = fields["title"] or row.node_key
    elif reference.kind == "sound_line":
        fields, sound_binding = sound_line_state(dao.session, project.id, reference.id, lock=True)
        revision = int(sound_binding["row_version"])
        name = f"{fields['character']} · {reference.id}"
    elif reference.kind in {"asset", "shot"}:
        row = (dao.asset if reference.kind == "asset" else dao.shot)(project_id, int(reference.id))
        if row is None:
            raise NotFound("已选主体已删除或不再可访问")
        fields = (_asset if reference.kind == "asset" else _shot)(row)
        for key in ("kind", "id", "revision"):
            fields.pop(key)
        if reference.kind == "shot":
            dialogue = dao.session.get(ShotDialogue, row.id)
            fields["dialogue"] = {
                "row_version": str(dialogue.row_version) if dialogue else "0",
                "document": deepcopy(dialogue.document)
                if dialogue
                else {"lines": [], "reviewed": False},
            }
        elif row.kind == "character":
            voice_binding = voice_binding_state(dao.session, project.id, row.id, lock=True)
            fields["voice"] = {key: voice_binding[key] for key in ("row_version", "media_id")}
        revision = row.row_version
        name = row.name if reference.kind == "asset" else row.title or f"分镜 {row.position}"
    else:
        slot = dao.image_slot(int(reference.id)) if reference.kind == "image_slot" else None
        group_id = slot.group_id if slot else int(reference.id)
        group = dao.image_group(project_id, group_id)
        if group is None or reference.kind == "image_slot" and slot is None:
            raise NotFound("已选图片组或图位不存在或无权访问")
        _group_target(dao, project_id, group)
        location.update(
            kind="asset" if group.asset_id is not None else "shot",
            id=str(group.asset_id or group.shot_id),
            image_group_id=str(group.id),
            **({"image_slot_id": str(slot.id)} if slot else {}),
        )
        if slot is not None:
            fields = {"group_id": str(group.id), **_slot_fields(slot)}
            revision = slot.item_version
            name = f"图片组 {group.id} · 图位 {slot.output_index}"
            media_ids = [slot.media_id] if slot.media_id else []
        else:
            slots = ImageGroupDAO(dao.session).items(group.id, lock=True)
            fields = {
                "target_kind": "asset" if group.asset_id is not None else "shot",
                "target_id": str(group.asset_id or group.shot_id),
                "expected_count": group.expected_count,
                "status": group.status,
                "primary_index": group.primary_index,
                "primary_version": str(group.primary_version),
                "items": [_slot_fields(item) for item in slots],
            }
            revision = group.primary_version
            name = f"图片组 {group.id}"
            media_ids = [
                item.media_id
                for item in slots
                if item.output_index == group.primary_index and item.media_id
            ]
    return (
        project,
        row if reference.kind == "node" else None,
        {
            "fields": fields,
            "revision": str(revision),
            "content_revision": str(content_revision) if content_revision is not None else None,
            "name": name,
            "checksum": digest(
                {"fields": fields, "private_revision": write_versions["private_revision"]}
            )
            if write_versions
            else digest({"fields": fields, "sound_binding": sound_binding})
            if sound_binding
            else digest(fields),
            "source_location": location,
            **({"write_versions": write_versions} if write_versions else {}),
            **({"voice_binding": voice_binding} if voice_binding else {}),
            **({"sound_binding": sound_binding} if sound_binding else {}),
        },
        media_ids,
    )


def freeze_subject(dao, owner, reference, *, check_revision=True):
    project, node, current, media_ids = _current(dao, owner, reference)
    if check_revision:
        expected = (
            reference.content_revision
            if reference.content_revision is not None
            else reference.revision
        )
        actual = (
            current["content_revision"]
            if reference.content_revision is not None
            else current["revision"]
        )
        if (
            actual != str(expected)
            or reference.checksum is not None
            and reference.checksum != current["checksum"]
        ):
            raise WorkflowError(
                "assistant_context_changed", "主体版本已变化，请核对后重新选入", 409
            )
    identity = reference.model_dump(
        mode="json", exclude={"revision", "content_revision", "checksum"}
    )
    key = subject_key(
        project.id, reference.source_kind, reference.source_id, reference.kind, reference.id
    )
    media = []
    if node is not None:
        if frozen := _freeze_node_media(dao, project.id, node):
            media.append(frozen)
    for media_id in media_ids:
        frozen = _freeze_node_media(
            dao,
            project.id,
            SimpleNamespace(
                kind="image",
                node_key=reference.id,
                content_json={"metadata": {"storageKey": f"resource:{media_id}"}},
            ),
        )
        media.append(frozen)
    for item in media:
        item["id"] = f"{key}:media:{item['media_id']}"
    result = {
        **identity,
        **current,
        "subject_key": key,
        "project_name": project.name,
        "allowed_fields": sorted(current["fields"]),
        "media": media,
    }
    if catalog := freeze_image_catalog(
        dao.session, owner, project.id, reference.kind, reference.id, lock=True
    ):
        result["image_catalog"] = catalog
    result["snapshot_id"] = digest(result)
    return result


def freeze_selection(session, owner, selection, previous):
    existing = {item["subject_key"]: item for item in snapshot_subjects(previous)}
    if any(key not in existing for key in selection.retain_keys):
        raise WorkflowError("selection_required", "只能保留本任务真实存在的主体引用", 409)
    result = [deepcopy(existing[key]) for key in selection.retain_keys]
    dao = AssistantContextDAO(session)
    for reference in sorted(
        selection.subjects, key=lambda r: (r.project_id, r.source_id, r.kind, r.id)
    ):
        result.append(freeze_subject(dao, owner, reference))
    if len({item["subject_key"] for item in result}) != len(result):
        raise WorkflowError("invalid_selection", "刷新主体时不能同时保留该主体的旧引用", 422)
    snapshot = {"schema_version": 2, "subjects": result}
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise WorkflowError(
            "agent_context_too_large", "选入主体的快照合计超过 1 MiB，请缩小范围", 422
        )
    return snapshot if result else None


def freeze_legacy_selection(session, owner, context):
    """Accept saved V3 page references without validating unrelated project/graph versions."""
    if context is None:
        return None
    dao = AssistantContextDAO(session)
    subjects = []
    for selected in context.selected:
        reference = TaskSubjectReference(
            project_id=context.project_id,
            source_kind=context.kind,
            source_id=context.id,
            kind=selected.kind,
            id=selected.id,
            revision=selected.revision or 1,
        )
        subjects.append(
            freeze_subject(dao, owner, reference, check_revision=selected.revision is not None)
        )
    snapshot = {"schema_version": 2, "subjects": subjects}
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise WorkflowError(
            "agent_context_too_large", "选入主体的快照合计超过 1 MiB，请缩小范围", 422
        )
    return snapshot if subjects else None


def selection_references(snapshot):
    return [{"type": "subject", **item} for item in subject_catalog(snapshot)]


def subject_directory(session, owner, project_id, kind, query, offset, limit):
    dao = AssistantContextDAO(session)
    project = _project(dao, owner, project_id, "canvas" if kind == "node" else "project")
    rows, total = dao.subject_directory(project.id, kind, query, offset, limit)
    items = []
    for row in rows:
        item = dict(row)
        if kind == "image_group":
            item["name"] = f"图片组 {row['id']}"
        elif kind == "image_slot":
            item["name"] = f"图片组 {item.pop('group_id')} · 图位 {item.pop('output_index')}"
        items.append(
            {
                **item,
                "id": str(item["id"]),
                "revision": str(item["revision"]),
                "content_revision": str(item["content_revision"])
                if item.get("content_revision")
                else None,
                "name": item.get("name") or ("未命名节点" if kind == "node" else "未命名分镜"),
                "project_id": str(project.id),
                "project_name": project.name,
                "kind": kind,
                "source_kind": "canvas" if kind == "node" else "project",
                "source_id": item.get("source_id", str(project.id)),
                "source_name": item.get("source_name", project.name),
            }
        )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def inspect_subjects(session, owner, references):
    dao = AssistantContextDAO(session)
    result = []
    for reference in references:
        _project_row, _node, current, _media_ids = _current(dao, owner, reference)
        result.append(
            {
                **reference.model_dump(mode="json"),
                "revision": current["revision"],
                "content_revision": current["content_revision"],
                "checksum": current["checksum"],
            }
        )
    return result


def selection_metadata(session, owner, snapshot):
    """Return availability and content hashes only; never replace the frozen body."""
    dao = AssistantContextDAO(session)
    result = []
    for subject in snapshot_subjects(snapshot):
        item = {
            k: v
            for k, v in subject.items()
            if k
            not in {
                "fields",
                "media",
                "write_versions",
                "image_catalog",
                "voice_binding",
                "sound_binding",
            }
        }
        try:
            reference = TaskSubjectReference.model_validate(
                {
                    key: subject[key]
                    for key in ("project_id", "source_kind", "source_id", "kind", "id", "revision")
                }
            )
            _project_row, _node, current, _media_ids = _current(dao, owner, reference)
            item.update(
                availability="current" if current["checksum"] == subject["checksum"] else "changed",
                current_revision=current["revision"],
                current_content_revision=current["content_revision"],
                current_checksum=current["checksum"],
                source_location=current["source_location"],
            )
        except NotFound:
            item["availability"] = "unavailable"
        result.append(item)
    return result


def read_selected_snapshot(session, owner, snapshot, values):
    subjects = snapshot_subjects(snapshot)
    keys = {item["subject_key"]: item for item in subjects}
    requested = []
    for identifier in values.ids:
        matches = [
            item
            for item in subjects
            if item["subject_key"] == identifier or item["id"] == identifier
        ]
        if len(matches) != 1:
            raise WorkflowError(
                "selection_required", "请使用本任务目录中的唯一 subject_key 读取主体", 403
            )
        requested.append(matches[0]["subject_key"])
    if len(set(requested)) != len(requested):
        raise WorkflowError("invalid_tool_arguments", "主体引用不能重复", 422)
    requested = requested or list(keys)
    status = {item["subject_key"]: item for item in selection_metadata(session, owner, snapshot)}
    result, remaining, continuation = [], values.character_limit, None
    for index, key in enumerate(requested):
        subject = keys[key]
        if status[key]["availability"] == "unavailable":
            raise NotFound("已选主体已删除或不再可访问，请移除引用或重新选择")
        text = json.dumps(subject["fields"], ensure_ascii=False, sort_keys=True)
        offset = values.character_offset if index == 0 else 0
        content = text[offset : offset + remaining]
        end = min(offset + len(content), len(text))
        result.append(
            {
                **status[key],
                "content": content,
                "source_changed": status[key]["availability"] == "changed",
                "range": {"offset": offset, "length": len(content), "total": len(text)},
            }
        )
        remaining -= len(content)
        if end < len(text):
            continuation = {"ids": requested[index:], "character_offset": end}
            break
        if remaining == 0 and index + 1 < len(requested):
            continuation = {"ids": requested[index + 1 :], "character_offset": 0}
            break
    return {"subjects": result, "next": continuation}
