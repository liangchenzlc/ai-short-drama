"""Native media source checks and business adoption; never schedules assistant execution."""

from copy import deepcopy

from short_drama.core.exceptions import BusinessError, Conflict
from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
from short_drama.service.assistant.operations.dialogue import (
    advance_sound_binding,
    dialogue_target_matches,
    sound_audio_state,
)
from short_drama.service.assistant.operations.media_reference import business_image_references
from short_drama.service.assistant.operations.voice import voice_binding_state, voice_target_matches
from short_drama.service.identity.authentication.actor import business_actor_locked


def business_media_may_submit(session, task, receipt, settings):
    target = session.info["business_media_targets"][task.id]
    references = target["reference_results"]
    if references:
        current = business_image_references(
            session,
            task.initiated_by,
            task.project_id,
            {int(item["media_id"]) for item in references},
            lock=True,
        )
        if any(current.get(int(item["media_id"])) != item for item in references):
            return _reject(
                session,
                task.id,
                "assistant_generation_reference_changed",
                "生成参考图的来源已失效，原请求尚未外发",
            )
    role = target.get("media_role")
    if role == "character_voice":
        matches = voice_target_matches(session, task.initiated_by, target, settings=settings)
        code = "assistant_voice_source_changed"
    elif role == "dialogue_audio":
        expected = advance_sound_binding(target["sound_binding"], target["id"], receipt.changes)
        matches = dialogue_target_matches(
            session, task.initiated_by, target, expected, settings=settings
        )
        code = "assistant_dialogue_source_changed"
    elif target["kind"] == "node":
        from short_drama.service.assistant.operations.canvas_media import canvas_target_matches

        matches = canvas_target_matches(
            session, task.initiated_by, target, task.service_type, task.id
        )
        code = "assistant_canvas_source_changed"
    elif task.service_type == "video":
        from short_drama.service.creation.storyboard.videos.video_admission import (
            shot_video_target_matches,
        )

        matches = shot_video_target_matches(session, task.initiated_by, target, settings=settings)
        code = "assistant_shot_video_source_changed"
    else:
        return True
    return matches or _reject(session, task.id, code, "生成来源已变化，原请求尚未外发")


def _reject(session, task_id, code, message):
    session.info.setdefault("assistant_submission_errors", {})[task_id] = {
        "code": code,
        "message": message,
    }
    return False


def adopt_business_media_locked(session, task, record, settings):
    target = record.config_snapshot.get("business_media")
    if not target:
        return
    data = record.response_data or {}
    if data.get("business_adoption") is not None:
        return
    dao = BusinessInvocationDAO(session)
    receipt = session.info["business_media_receipts"].get(task.id)
    try:
        if task.cancel_requested or receipt is None:
            raise Conflict("原生媒体受理已不可采用，生成产物保留在候选中")
        actor = business_actor_locked(session, task.initiated_by, f"business-media:{receipt.id}")
        previous = {key: session.info.get(key) for key in ("actor", "resource_scope")}
        session.info["actor"] = actor
        session.info.pop("resource_scope", None)
        try:
            if target.get("media_role") in {"character_voice", "dialogue_audio"}:
                if task.status != "succeeded":
                    raise Conflict("音频媒体未全部保存，已保存产物保留在候选中")
                with session.begin_nested():
                    if target["media_role"] == "character_voice":
                        changes = [_adopt_voice(session, task, record, target, settings)]
                    else:
                        changes = [_adopt_dialogue(session, task, target, receipt, settings)]
            else:
                from short_drama.service.generations.runtime.business_adoption import (
                    native_adoption_changes_locked,
                )

                changes = native_adoption_changes_locked(session, task, record, target)
        finally:
            for key, value in previous.items():
                if value is None:
                    session.info.pop(key, None)
                else:
                    session.info[key] = value
        receipt.changes = [*receipt.changes, *changes]
        # 这里只同步采用审计以支持撤销；提交回执及 Run 的生命周期均不变。
        for operation in dao.media_operations(receipt.id):
            operation.changes = deepcopy(receipt.changes)
        adoption = {"adopted": True, "changes": deepcopy(changes)}
    except BusinessError as error:
        adoption = {"adopted": False, "code": error.code}
    record.response_data = {**data, "business_adoption": adoption}


def _adopt_voice(session, task, record, target, settings):
    from short_drama.service.creation.sound.voices.voices import NativeVoiceService

    if not voice_target_matches(session, task.initiated_by, target, check_flags=False):
        raise Conflict("角色或当前音色已变化，生成样音已保留")
    before = voice_binding_state(session, task.project_id, target["id"], lock=True)
    NativeVoiceService(session, settings).adopt_locked(
        task.project_id,
        target["id"],
        {"row_version": int(before["row_version"]), "record_id": str(record.id)},
    )
    return {
        "kind": "character_voice",
        "id": target["id"],
        "target_version": target["row_version"],
        "before": before,
        "after": voice_binding_state(session, task.project_id, target["id"], lock=True),
    }


def _adopt_dialogue(session, task, target, receipt, settings):
    from short_drama.service.creation.sound.tracks.sound import ProjectSoundService

    expected = advance_sound_binding(target["sound_binding"], target["id"], receipt.changes)
    if not dialogue_target_matches(
        session, task.initiated_by, target, expected, settings=settings, check_flags=False
    ):
        raise Conflict("台词、声音文档或时间线已变化，配音已保留")
    outputs = BusinessInvocationDAO(session).generation_outputs([task.id])[task.id]
    if len(outputs) != 1:
        raise Conflict("配音产物数量与受理目标不一致，已保存产物保留在候选中")
    before = sound_audio_state(session, task.project_id, target["id"])
    ProjectSoundService(session, settings).adopt_locked(
        task.project_id,
        {
            "line_id": target["id"],
            "media_id": outputs[0]["media_id"],
            "row_version": expected["row_version"],
        },
        assembly_version=int(expected["assembly_version"]),
        timeline_hash=expected["timeline_hash"],
    )
    return {
        "kind": "sound_line",
        "id": target["id"],
        "media_role": "dialogue_audio",
        "before": before,
        "after": sound_audio_state(session, task.project_id, target["id"]),
    }
