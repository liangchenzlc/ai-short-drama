"""Reverse selected text changes while retaining the current native multitrack document."""

from copy import deepcopy
from typing import Any

from short_drama.core.exceptions import BusinessError
from short_drama.domain import Project
from short_drama.domain.assistant.undo.undo import AgentBatchUndo
from short_drama.schemas.base import UINT64_MAX
from short_drama.schemas.creation.sound.tracks.sound import SoundDocument, SoundEdit
from short_drama.service.assistant.operations.dialogue import (
    COMMON_VERSIONS,
    SOUND_FIELDS,
    advance_sound_binding,
    sound_audio_state,
    sound_document_binding,
    sound_line_state,
)
from short_drama.service.assistant.undo.undo_chain import advance_document, advance_snapshot
from short_drama.service.creation.sound.tracks.sound import ProjectSoundService
from short_drama.service.creation.sound.voices.voices import project_mode


def preview_sound_change(
    service: Any,
    project: Project,
    change: dict[str, Any],
    receipts: list[AgentBatchUndo],
    changes: list[dict[str, Any]] | None = None,
    *,
    operation_id: int,
) -> tuple[str | None, dict[str, Any] | None, set[str]]:
    try:
        native = ProjectSoundService(service.session, service.settings)
        creation, assembly, sound, _video = native._scope(project.id)
        if project_mode(service.session, creation.id, service.settings) == "native":
            return "当前为原生有声模式，不能修改独立配音台词", None, set()
        fields, binding = sound_line_state(service.session, project.id, change["id"], lock=True)
    except BusinessError:
        return "原声音文档或台词已不可用", None, set()
    current, expected = {**fields, **binding}, advance_snapshot(change, receipts, operation_id)
    if change.get("media_role") == "dialogue_audio":
        if not all(field in expected for field in (*SOUND_FIELDS, "audio_clips")):
            return "旧配音回执缺少台词和轨道差异，不能安全回退", current, set()
        current = sound_audio_state(service.session, project.id, change["id"])
        expected = advance_sound_binding(expected, change["id"], changes or [])
    expected.update(
        advance_document(
            {key: expected[key] for key in COMMON_VERSIONS},
            "sound_document",
            expected["assembly_id"],
            receipts,
            operation_id,
        )
    )
    reason = None
    if {key: value for key, value in current.items() if key != "clip_positions"} != {
        key: value for key, value in expected.items() if key != "clip_positions"
    }:
        reason = "台词、媒体、声音版本或画面已有后续变化"
    elif sound.row_version == UINT64_MAX or assembly.row_version == UINT64_MAX:
        reason = "声音或画面版本已达到上限"
    elif change["before"] is None and any(
        clip.get("dialogue_id") == change["id"] for clip in sound.document.get("audio_clips", [])
    ):
        reason = "新台词仍被声音轨道引用"
    return reason, current, set()


def restore_sound_locked(
    service: Any,
    project: Project,
    changes: dict[str, dict[str, Any]],
    selected: set[str],
    receipt_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    session = service.session
    native = ProjectSoundService(session, service.settings)
    creation, assembly, sound, video = native._scope(project.id)
    before_binding = sound_document_binding(session, project.id, lock=True)
    document = deepcopy(sound.document)
    snapshots, refs, inverse = {}, [], []
    restored_adoptions = {}
    for key in sorted(selected):
        change = changes[key]
        fields, binding = sound_line_state(session, project.id, change["id"], lock=True)
        audio = change.get("media_role") == "dialogue_audio"
        snapshots[change["id"]] = (
            sound_audio_state(session, project.id, change["id"]) if audio else {**fields, **binding}
        )
        if change["before"] is None:
            document["dialogue"] = [
                line for line in document["dialogue"] if line["id"] != change["id"]
            ]
        else:
            line = next(line for line in document["dialogue"] if line["id"] == change["id"])
            line.update({field: deepcopy(change["before"][field]) for field in SOUND_FIELDS})
            if audio:
                restored_adoptions[change["id"]] = (line["media_id"], line["adopted_hash"])
                affected = {
                    clip["id"]
                    for state in (change["before"], change["after"])
                    for clip in state["audio_clips"]
                }
                document["audio_clips"] = [
                    clip for clip in document["audio_clips"] if clip["id"] not in affected
                ]
                for position, clip in zip(
                    change["before"]["clip_positions"], change["before"]["audio_clips"], strict=True
                ):
                    document["audio_clips"].insert(
                        min(position, len(document["audio_clips"])), deepcopy(clip)
                    )
    native._save_in_transaction(
        creation,
        assembly,
        sound,
        video,
        SoundEdit(
            row_version=sound.row_version,
            timeline_hash=before_binding["timeline_hash"],
            request_id=f"assistant-undo:{receipt_id}",
            document=SoundDocument.model_validate(document),
        ),
        read_result=False,
        restored_adoptions=restored_adoptions,
    )
    after_binding = sound_document_binding(session, project.id, lock=True)
    for key in sorted(selected):
        change = changes[key]
        after = None
        if change["before"] is not None:
            fields, binding = sound_line_state(session, project.id, change["id"], lock=True)
            after = (
                sound_audio_state(session, project.id, change["id"])
                if change.get("media_role") == "dialogue_audio"
                else {**fields, **binding}
            )
        inverse.append(
            {
                "kind": "sound_line",
                "id": change["id"],
                "before": snapshots[change["id"]],
                "after": after,
                **({"media_role": change["media_role"]} if change.get("media_role") else {}),
            }
        )
        refs.append(
            {
                "kind": "sound_line",
                "id": change["id"],
                "project_id": str(project.id),
                "name": f"{snapshots[change['id']]['character']} · {change['id']}",
                "row_version": after_binding["row_version"],
                "assembly_version": after_binding["assembly_version"],
                "status": "deleted" if after is None else "updated",
                **(
                    {"media_id": after["media_id"], "media_role": "dialogue_audio"}
                    if after and change.get("media_role") == "dialogue_audio"
                    else {}
                ),
            }
        )
    inverse.append(
        {
            "kind": "sound_document",
            "id": str(assembly.id),
            "before": before_binding,
            "after": after_binding,
        }
    )
    return inverse, refs
