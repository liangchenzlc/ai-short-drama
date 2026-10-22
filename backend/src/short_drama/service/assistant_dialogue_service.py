"""Selected legacy sound lines, frozen dubbing sources and protected atomic adoption."""

from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.agent.authorization import digest
from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.dao.assistant_context_dao import AssistantContextDAO
from short_drama.domain import AIModelConfig, AsyncTask, MediaAsset, Project
from short_drama.domain.agent_media import AgentMediaRequest
from short_drama.domain.project_assembly import ProjectAssembly
from short_drama.domain.project_sound import ProjectSound
from short_drama.schemas.assistant_media import AssistantDialogueAudioRequest
from short_drama.schemas.assistant_standard import StandardPatch
from short_drama.schemas.base import UINT64_MAX
from short_drama.schemas.project_sound import Dialogue, SoundDocument, SoundEdit
from short_drama.service.native_voice_service import project_mode
from short_drama.service.project_assembly_service import ProjectAssemblyService
from short_drama.service.project_sound_service import ProjectSoundService, line_hash

SOUND_FIELDS = frozenset(
    {"id", "character", "text", "voice", "config_id", "start_ms", "media_id", "adopted_hash"}
)
COMMON_VERSIONS = ("assembly_id", "assembly_version", "row_version", "timeline_hash")
SOUND_ACTIONS = frozenset({"create_sound_line", "update_sound_line"})


def sound_document_binding(
    session: Session, project_id: int | str, *, lock: bool = False
) -> dict[str, str] | None:
    """Freeze versions only, including a not-yet-created sound document; never read its text."""
    query = select(ProjectAssembly).where(ProjectAssembly.project_id == int(project_id))
    if lock:
        query = query.with_for_update()
    assembly = session.scalar(query.execution_options(populate_existing=True))
    if assembly is None:
        return None
    version_query = select(ProjectSound.row_version).where(ProjectSound.assembly_id == assembly.id)
    if lock:
        version_query = version_query.with_for_update()
    sound_version = session.scalar(version_query)
    service = ProjectAssemblyService(session)
    video = service._video_snapshot(assembly, service._clips(assembly))
    return {
        "assembly_id": str(assembly.id),
        "assembly_version": str(assembly.row_version),
        "row_version": str(sound_version if sound_version is not None else 0),
        "timeline_hash": digest(video),
    }


def sound_line_state(
    session: Session,
    project_id: int | str,
    line_id: str,
    *,
    lock: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    scope = AssistantContextDAO(session).sound_scope(int(project_id), lock=lock)
    if scope is None:
        raise NotFound("声音草稿不存在或不再可访问")
    assembly, sound = scope
    line = next(
        (line for line in sound.document.get("dialogue", []) if line["id"] == line_id), None
    )
    if line is None:
        raise NotFound("已选台词已删除或不再可访问")
    fields = {key: deepcopy(line.get(key)) for key in SOUND_FIELDS}
    service = ProjectAssemblyService(session)
    video = service._video_snapshot(assembly, service._clips(assembly))
    return fields, {
        "assembly_id": str(assembly.id),
        "assembly_version": str(assembly.row_version),
        "row_version": str(sound.row_version),
        "timeline_hash": digest(video),
        "line_hash": line_hash(line),
        "line_checksum": digest(fields),
    }


def sound_audio_state(session: Session, project_id: int | str, line_id: str) -> dict[str, Any]:
    fields, binding = sound_line_state(session, project_id, line_id, lock=True)
    _assembly, sound = AssistantContextDAO(session).sound_scope(int(project_id), lock=True)
    clips = [
        (index, clip)
        for index, clip in enumerate(sound.document.get("audio_clips", []))
        if clip.get("dialogue_id") == line_id
        or fields["media_id"] is not None
        and clip["kind"] == "dialogue"
        and clip["media_id"] == fields["media_id"]
        and clip["start_ms"] == fields["start_ms"]
    ]
    return {
        **fields,
        **binding,
        "audio_clips": [deepcopy(clip) for _index, clip in clips],
        "clip_positions": [index for index, _clip in clips],
    }


def advance_sound_binding(
    binding: dict[str, str],
    line_id: str,
    changes: list[dict[str, Any]],
) -> dict[str, str]:
    """Advance only a continuous chain of this task's committed native adoptions."""
    current = deepcopy(binding)
    ordered = sorted(
        (
            change
            for change in changes
            if change["kind"] in {"sound_line", "sound_document"} and change["before"] is not None
        ),
        key=lambda change: int(change["before"]["row_version"]),
    )
    for change in ordered:
        before = change["before"]
        if any(current[key] != before[key] for key in COMMON_VERSIONS):
            continue
        peers = [
            item
            for item in ordered
            if all(item["before"][key] == before[key] for key in COMMON_VERSIONS)
        ]
        after = change["after"]
        current.update({key: after[key] for key in COMMON_VERSIONS})
        for item in peers:
            if item["kind"] == "sound_line" and item["id"] == line_id:
                current.update({key: item["after"][key] for key in ("line_hash", "line_checksum")})
    return current


def apply_sound_lines_locked(
    session: Session,
    project_id: int,
    batch: StandardPatch,
    operation_id: int,
    allowed_line_ids: set[str],
    settings: Settings,
    owner_user_id: int,
) -> dict[str, Any]:
    actor = session.info.get("actor")
    if actor is not None and actor.user_id != owner_user_id:
        raise NotFound("台词写入账号不匹配")
    original = {
        key: session.info[key] for key in ("actor", "resource_scope") if key in session.info
    }
    session.info["actor"] = actor or ActorContext(
        owner_user_id, "", "", True, 0, "", f"assistant-sound:{operation_id}"
    )
    try:
        return _apply_sound_lines_locked(
            session, project_id, batch, operation_id, allowed_line_ids, settings
        )
    finally:
        for key in ("actor", "resource_scope"):
            if key in original:
                session.info[key] = original[key]
            else:
                session.info.pop(key, None)


def _apply_sound_lines_locked(
    session: Session,
    project_id: int,
    batch: StandardPatch,
    operation_id: int,
    allowed_line_ids: set[str],
    settings: Settings,
) -> dict[str, Any]:
    """Merge authorized source fields into the real document and reuse its native save rules."""
    service = ProjectSoundService(session, settings)
    creation, assembly, sound, video = service._scope(project_id)
    if project_mode(session, creation.id, settings) == "native":
        raise Conflict("原生视频模式请编辑分镜对白，不能修改独立配音台词")
    before_binding = sound_document_binding(session, project_id, lock=True)
    expected = batch.sound_revision.model_dump(mode="json")
    if any(before_binding[key] != expected[key] for key in expected):
        raise Conflict("声音草稿或画面已变化，请核对并主动刷新引用")
    if any(int(before_binding[key]) == UINT64_MAX for key in ("row_version", "assembly_version")):
        raise Conflict("声音或画面版本已达到上限，不能保存台词")
    document = deepcopy(sound.document) if sound else SoundDocument().model_dump(mode="json")
    lines = {line["id"]: line for line in document["dialogue"]}
    temporary, originals = {}, {}
    for index, action in enumerate(batch.actions):
        if action.action == "create_sound_line":
            identifier = str(uuid5(NAMESPACE_URL, f"assistant-sound:{operation_id}:{index}"))
            if identifier in lines:
                raise Conflict("台词创建身份已存在，请核对原操作回执")
            line = Dialogue(id=identifier, **action.values.model_dump()).model_dump(mode="json")
            lines[identifier] = line
            document["dialogue"].append(line)
            temporary[action.key] = identifier
            originals[identifier] = None
        elif action.action == "update_sound_line":
            identifier = (
                temporary.get(action.line[1:]) if action.line.startswith("$") else action.line
            )
            if identifier not in lines:
                raise NotFound("获准台词已删除或不属于写入作品")
            if identifier not in allowed_line_ids and identifier not in temporary.values():
                raise WorkflowError("assistant_write_scope", "台词未在明确授权范围内", 403)
            line = lines[identifier]
            originals.setdefault(
                identifier,
                {
                    **deepcopy(line),
                    **before_binding,
                    "line_hash": line_hash(line),
                    "line_checksum": digest({key: line.get(key) for key in SOUND_FIELDS}),
                },
            )
            line.update(action.patch.model_dump(mode="json", exclude_unset=True))
    validated = SoundDocument.model_validate(document)
    if sound is not None and validated.model_dump(mode="json") == sound.document:
        return {
            "changes": [],
            "result_refs": [
                {
                    "kind": "sound_line",
                    "id": identifier,
                    "project_id": str(project_id),
                    "name": f"{lines[identifier]['character']} · {identifier}",
                    "row_version": before_binding["row_version"],
                    "assembly_version": before_binding["assembly_version"],
                    "status": "unchanged",
                }
                for identifier in originals
            ],
            "temporary_ids": {},
            "sound_revision": expected,
        }
    service._save_in_transaction(
        creation,
        assembly,
        sound,
        video,
        SoundEdit(
            row_version=batch.sound_revision.row_version,
            timeline_hash=batch.sound_revision.timeline_hash,
            request_id=f"assistant-sound:{operation_id}",
            document=validated,
        ),
        read_result=False,
    )
    after_binding = sound_document_binding(session, project_id, lock=True)
    changes, refs = [], []
    for identifier, before in originals.items():
        fields, binding = sound_line_state(session, project_id, identifier, lock=True)
        after = {**fields, **binding}
        changes.append({"kind": "sound_line", "id": identifier, "before": before, "after": after})
        refs.append(
            {
                "kind": "sound_line",
                "id": identifier,
                "project_id": str(project_id),
                "name": f"{fields['character']} · {identifier}",
                "row_version": binding["row_version"],
                "assembly_version": binding["assembly_version"],
                "status": "created" if before is None else "updated",
            }
        )
    changes.append(
        {
            "kind": "sound_document",
            "id": str(assembly.id),
            "before": before_binding,
            "after": after_binding,
        }
    )
    return {
        "changes": changes,
        "result_refs": refs,
        "temporary_ids": temporary,
        "sound_revision": {key: after_binding[key] for key in expected},
    }


def _current(
    session: Session,
    owner: int,
    project_id: int | str,
    line_id: str,
    *,
    settings: Settings | None = None,
    check_flags: bool = True,
) -> tuple[dict[str, Any], dict[str, str]]:
    if check_flags and not getattr(settings, "audio_production_enabled", False):
        raise WorkflowError("assistant_dialogue_disabled", "台词配音功能尚未启用", 409)
    project = session.scalar(
        select(Project)
        .where(
            Project.id == int(project_id),
            Project.owner_user_id == owner,
            Project.workspace_mode == "standard",
            Project.archived_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise NotFound("台词目标作品不存在或不再可访问")
    if project_mode(session, project.id, settings) == "native":
        raise Conflict("原生视频模式不生成独立台词配音")
    return sound_line_state(session, project.id, line_id, lock=True)


def prepare_dialogue_audio_locked(
    session: Session,
    owner: int,
    project_id: str,
    values: AssistantDialogueAudioRequest,
    trusted_version: int,
    *,
    trusted_sound: dict[str, str],
    settings: Settings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fields, binding = _current(session, owner, project_id, values.target_id, settings=settings)
    if binding != trusted_sound or str(values.row_version) != str(trusted_version):
        raise Conflict("台词、声音文档或画面时间线在选入后已变化，请主动刷新引用")
    if (
        str(values.assembly_version) != binding["assembly_version"]
        or values.timeline_hash != binding["timeline_hash"]
    ):
        raise Conflict("配音需要目录中的可信声音及画面版本")
    if any(int(binding[key]) == UINT64_MAX for key in ("row_version", "assembly_version")):
        raise Conflict("声音或画面版本已达到上限，无法采用配音")
    model = session.scalar(
        select(AIModelConfig)
        .where(
            AIModelConfig.id == values.model_config_id,
            AIModelConfig.owner_user_id == owner,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if model is None or model.service_type != "audio" or not model.enabled or model.is_deleted:
        raise WorkflowError("assistant_model_unavailable", "本人配音模型已停用或不可用", 409)
    if model.row_version != values.model_version or str(model.id) != fields["config_id"]:
        raise Conflict("配音必须使用已保存台词的模型及当前配置版本")
    if not fields["text"].strip() or not fields["voice"].strip():
        raise Conflict("请先保存台词正文和音色")
    return {
        "project_id": str(project_id),
        "config_id": str(model.id),
        "source": {
            "scene": "dialogue_audio",
            "project_id": str(project_id),
            "line_id": values.target_id,
            "row_version": binding["row_version"],
        },
    }, {
        "kind": "sound_line",
        "media_role": "dialogue_audio",
        "id": values.target_id,
        "project_id": str(project_id),
        "name": f"{fields['character']} · {values.target_id}",
        "row_version": binding["row_version"],
        "sound_binding": binding,
        "provider_input": {"text": fields["text"]},
        "provider_parameters": {"voice": fields["voice"]},
    }


def _expected_binding(session: Session, intent: AgentMediaRequest) -> dict[str, str]:
    peers = session.scalars(
        select(AgentMediaRequest)
        .where(
            AgentMediaRequest.operation_id == intent.operation_id,
            AgentMediaRequest.id != intent.id,
        )
        .order_by(AgentMediaRequest.item_index)
    ).all()
    return advance_sound_binding(
        intent.frozen_target["sound_binding"],
        intent.frozen_target["id"],
        [
            {
                "kind": "sound_line",
                "id": peer.frozen_target["id"],
                "before": peer.result["before_binding"],
                "after": peer.result["after_binding"],
            }
            for peer in peers
            if peer.frozen_target.get("media_role") == "dialogue_audio"
            and (peer.result or {}).get("adopted")
        ],
    )


def dialogue_audio_may_submit(
    session: Session,
    owner: int,
    intent: AgentMediaRequest,
    *,
    settings: Settings | None = None,
    check_flags: bool = True,
) -> bool:
    target = intent.frozen_target
    try:
        _fields, binding = _current(
            session,
            owner,
            target["project_id"],
            target["id"],
            settings=settings,
            check_flags=check_flags,
        )
    except BusinessError:
        return False
    return binding == _expected_binding(session, intent)


def settle_dialogue_audio_locked(
    session: Session,
    owner: int,
    intent: AgentMediaRequest,
    native: AsyncTask,
    outputs: list[MediaAsset],
    settings: Settings,
) -> tuple[dict[str, Any], str, dict[str, str] | None]:
    target = intent.frozen_target
    try:
        _current(
            session, owner, target["project_id"], target["id"], settings=settings, check_flags=False
        )
        available = True
    except BusinessError:
        available = False
    result = {
        "adopted": False,
        "target_available": available,
        "target_version": target["row_version"],
        "items": [
            {
                "id": str(output.id),
                "media_id": str(output.media_id),
                "output_index": output.output_index,
                "status": "ready",
            }
            for output in outputs
        ],
    }
    if native.status != "succeeded" or len(outputs) != 1:
        return (
            result,
            "failed",
            {
                "code": "assistant_media_partial_failure",
                "message": "台词配音未生成成功，已保存产物保留在私人任务中",
            },
        )
    if not dialogue_audio_may_submit(session, owner, intent, settings=settings, check_flags=False):
        return (
            result,
            "conflict",
            {
                "code": "assistant_media_adoption_conflict",
                "message": "台词、声音文档或时间线已变化，配音已保留",
            },
        )
    actor = session.info.get("actor")
    if actor is not None and actor.user_id != owner:
        raise NotFound("配音采用账号不匹配")
    original = {
        key: session.info[key] for key in ("actor", "resource_scope") if key in session.info
    }
    session.info["actor"] = actor or ActorContext(
        owner, "", "", True, 0, "", f"assistant-media:{intent.operation_id}"
    )
    before = _expected_binding(session, intent)
    undo_before = sound_audio_state(session, target["project_id"], target["id"])
    try:
        try:
            with session.begin_nested():
                ProjectSoundService(session, settings).adopt_locked(
                    target["project_id"],
                    {
                        "line_id": target["id"],
                        "media_id": str(outputs[0].media_id),
                        "row_version": before["row_version"],
                    },
                    assembly_version=int(before["assembly_version"]),
                    timeline_hash=before["timeline_hash"],
                )
                _fields, after = sound_line_state(
                    session, target["project_id"], target["id"], lock=True
                )
                undo_after = sound_audio_state(session, target["project_id"], target["id"])
        except BusinessError as error:
            return result, "conflict", {"code": error.code, "message": error.message}
        result.update(
            adopted=True,
            target_version=after["row_version"],
            before_binding=before,
            after_binding=after,
            undo_before=undo_before,
            undo_after=undo_after,
        )
        return result, "succeeded", None
    finally:
        for key in ("actor", "resource_scope"):
            if key in original:
                session.info[key] = original[key]
            else:
                session.info.pop(key, None)


def dialogue_audio_changes_locked(
    session: Session, intent: AgentMediaRequest
) -> list[dict[str, Any]]:
    if not (intent.result or {}).get("adopted"):
        return []
    return [
        {
            "kind": "sound_line",
            "id": intent.frozen_target["id"],
            "media_role": "dialogue_audio",
            "before": deepcopy(intent.result.get("undo_before", intent.result["before_binding"])),
            "after": deepcopy(intent.result.get("undo_after", intent.result["after_binding"])),
        }
    ]
