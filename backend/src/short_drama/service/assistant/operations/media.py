"""Native standard media admission for authorized assistant targets, without external I/O."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import AIModelConfig, Asset, ShotScript
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.assistant.operations.image import image_adoption_state
from short_drama.service.generations.admission.context import GenerationContextService
from short_drama.service.generations.admission.generation import AIGenerationService
from short_drama.service.generations.admission.project_scope import lock_media_project
from short_drama.service.identity.authentication.actor import business_actor_locked


def prepare_image_locked(session, owner, project_id, values, trusted_version):
    project = lock_media_project(session, project_id, owner)
    model = session.scalar(
        select(AIModelConfig)
        .where(AIModelConfig.id == values.model_config_id, AIModelConfig.owner_user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if model is None or model.service_type != "image" or not model.enabled or model.is_deleted:
        raise WorkflowError("assistant_model_unavailable", "本人图片模型已停用或不可用", 409)
    if str(model.row_version) != str(values.model_version):
        raise Conflict("图片模型配置已变化，请重新核对")
    target = session.scalar(
        select(Asset if values.target_kind == "asset" else ShotScript)
        .where(
            (Asset if values.target_kind == "asset" else ShotScript).id == values.target_id,
            (Asset if values.target_kind == "asset" else ShotScript).project_id == project.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None or target.deleted_at is not None:
        raise NotFound("生成目标已删除或归档")
    if str(values.row_version) != str(trusted_version) or target.row_version != trusted_version:
        raise Conflict("生成目标在选入后已变化，请核对并主动刷新引用")
    if target.row_version == UINT64_MAX:
        raise Conflict("生成目标版本已达到上限，无法回填图片")
    source = {"scene": f"{values.target_kind}_image", f"{values.target_kind}_id": str(target.id)}
    parameters = {"count": values.count, "aspect": values.aspect, "resolution": values.resolution}
    if values.target_kind == "asset":
        source.update(row_version=str(target.row_version), project_id=str(project.id))
    else:
        _project, _shot, _assets, _snapshot, context_hash = GenerationContextService(
            session
        ).locked_shot_context(target.id)
        saved = target.image_settings or {"aspect": None, "resolution": "2K", "layout": "single"}
        if values.aspect != saved["aspect"] or values.resolution != saved["resolution"]:
            raise Conflict("图片参数与已保存分镜设置不一致，请先明确修改设置")
        source.update(
            context_mode="saved",
            row_version=str(target.row_version),
            context_hash=context_hash,
            layout=saved["layout"],
        )
    payload = {
        "project_id": str(project.id),
        "config_id": str(model.id),
        "source": source,
        "input": {"prompt": values.prompt, "reference_media_ids": list(values.reference_media_ids)},
        "parameters": parameters,
    }
    return payload, {
        "project_id": str(project.id),
        "kind": values.target_kind,
        "id": str(target.id),
        "row_version": str(target.row_version),
        "before": image_adoption_state(session, target, values.target_kind),
    }


def admit_media_locked(session, owner, intent, settings):
    actor = session.info.get("actor")
    if actor is not None and actor.user_id != owner:
        raise NotFound("媒体生成账号不匹配")
    original = {
        key: session.info[key] for key in ("actor", "resource_scope") if key in session.info
    }
    session.info["actor"] = actor or business_actor_locked(
        session, user_id=owner, request_id=f"assistant-media:{intent.operation_id}"
    )

    def authorized_input(request):
        # Native preparation reads hashes/structure for validation. Only the explicit
        # tool prompt and selected references cross the provider boundary.
        snapshot = request.get("source_snapshot") or {}
        if intent.frozen_target.get("media_role") == "dialogue_audio":
            target = intent.frozen_target
            if (
                request.get("input") != target["provider_input"]
                or request.get("parameters") != target["provider_parameters"]
                or snapshot.get("line_hash") != target["sound_binding"]["line_hash"]
            ):
                raise Conflict("已保存台词或音色与冻结来源不一致，未受理配音")
            request["source_snapshot"] = {
                "line_hash": snapshot["line_hash"],
            }
            request.pop("business_intent", None)
            return request
        native = snapshot.get("native_speech")
        if (
            intent.media_kind == "video"
            and native is not None
            and native != intent.frozen_target.get("native_speech")
        ):
            raise WorkflowError(
                "assistant_native_video_scope", "原生有声视频需先明确核对对白与音色来源", 409
            )
        request["input"] = deepcopy(
            intent.frozen_request.get("input") or intent.frozen_target["provider_input"]
        )
        request["source_snapshot"] = {
            key: value
            for key, value in snapshot.items()
            if key
            in {
                "asset_row_version",
                "asset_content_hash",
                "context_hash",
                "row_version",
                "shot_context_hash",
                "reference_media_id",
                "reference_layout",
                "input_mode",
                "native_speech",
            }
        }
        request.pop("business_intent", None)
        return request

    try:
        summary, _created = AIGenerationService(session, settings).create_locked(
            intent.media_kind,
            intent.frozen_request,
            f"assistant-media:{intent.id}",
            prepare_transform=authorized_input,
            agent_managed=True,
            assistant_managed=True,
        )
        intent.generation_task_id = int(summary["generation_id"])
        intent.status = "accepted"
        session.flush()
        return summary
    finally:
        for key in ("actor", "resource_scope"):
            if key in original:
                session.info[key] = original[key]
            else:
                session.info.pop(key, None)


def submit_generation_batch_locked(session, identity, values, receipt_id, settings):
    """Admit native tasks atomically; no assistant execution or supplier I/O is created here."""
    from short_drama.core.config import Settings
    from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
    from short_drama.dao.generations.results.records import AIGenerationRecordDAO
    from short_drama.schemas.canvas.generation.tasks.task_runtime import CanvasRuntimeTaskCreate
    from short_drama.service.assistant.operations.canvas_media import prepare_canvas_media_locked
    from short_drama.service.assistant.operations.dialogue import (
        prepare_dialogue_audio_locked,
        sound_line_state,
    )
    from short_drama.service.assistant.operations.invocation import require_invocation_subject
    from short_drama.service.assistant.operations.media_reference import business_image_references
    from short_drama.service.assistant.operations.shot_video import prepare_shot_video_locked
    from short_drama.service.assistant.operations.voice import (
        prepare_voice_design_locked,
        voice_binding_state,
    )
    from short_drama.service.canvas.generation.generation import CanvasGenerationService

    settings = settings or Settings()
    owner = identity.actor.user_id
    references = set()
    if identity.scope.subject_policy == "project":
        requested = {
            identifier for item in values.requests for identifier in item.reference_media_ids
        }
        references.update(
            BusinessInvocationDAO(session).project_images(values.project_id, requested)
        )
    else:
        references.update(
            int(slot["media_id"])
            for catalog in identity.scope.image_catalogs.values()
            for group in catalog["groups"]
            for slot in group["items"]
            if slot["media_id"]
        )
        references.update(
            int(media["media_id"])
            for source in identity.scope.subject_snapshots.values()
            for media in source.get("media", [])
            if media.get("kind") == "image"
        )
    prepared = []
    for item in values.requests:
        kind = {
            "character_voice": "asset",
            "dialogue_audio": "sound_line",
            "shot_video": "shot",
            "node": "canvas_node",
        }.get(item.target_kind, item.target_kind)
        require_invocation_subject(identity, kind, item.target_id)
        source = identity.scope.subject_snapshots.get(f"{kind}:{item.target_id}")
        if identity.scope.subject_policy == "selected" and source is None:
            raise Conflict("生成目标缺少可信主体快照，请主动刷新引用")
        trusted_version = int(source["revision"]) if source else item.row_version
        if not set(item.reference_media_ids).issubset(references):
            raise WorkflowError(
                "business_media_reference_scope", "参考图片未包含在本次授权对象中", 403
            )
        kwargs = {}
        if item.target_kind == "node":
            if values.canvas_id is None:
                raise WorkflowError("business_canvas_required", "画布媒体需要明确画布身份", 422)
            payload, target = prepare_canvas_media_locked(
                session,
                owner,
                {"project_id": str(values.project_id), "canvas_id": values.canvas_id},
                item,
                source["write_versions"]
                if source
                else {
                    "row_version": str(item.row_version),
                    "private_revision": str(item.private_revision),
                },
            )
        else:
            prepare = {
                "asset": prepare_image_locked,
                "shot": prepare_image_locked,
                "shot_video": prepare_shot_video_locked,
                "character_voice": prepare_voice_design_locked,
                "dialogue_audio": prepare_dialogue_audio_locked,
            }[item.target_kind]
            if item.target_kind == "character_voice":
                kwargs = {
                    "trusted_voice": source.get("voice_binding")
                    if source
                    else voice_binding_state(session, values.project_id, item.target_id, lock=True),
                    "settings": settings,
                }
            elif item.target_kind == "dialogue_audio":
                sound = (
                    source.get("sound_binding")
                    if source
                    else sound_line_state(session, values.project_id, item.target_id, lock=True)[1]
                )
                if sound is None:
                    raise Conflict("台词缺少可信声音快照，请主动刷新引用")
                kwargs = {"trusted_sound": sound, "settings": settings}
            elif item.target_kind == "shot_video":
                kwargs = {"settings": settings}
                if item.native_dialogue_version is not None:
                    from short_drama.domain import ShotDialogue

                    dialogue = source.get("fields", {}).get("dialogue") if source else None
                    if source is None:
                        row = session.get(ShotDialogue, item.target_id, with_for_update=True)
                        dialogue = (
                            {"row_version": str(row.row_version), "document": row.document}
                            if row
                            else None
                        )
                    voices = {}
                    for voice in item.native_voice_versions:
                        require_invocation_subject(identity, "asset", voice.character_id)
                        character = identity.scope.subject_snapshots.get(
                            f"asset:{voice.character_id}"
                        )
                        voices[str(voice.character_id)] = {
                            "row_version": character["revision"]
                            if character
                            else str(voice.row_version),
                            "voice_binding": character.get("voice_binding")
                            if character
                            else voice_binding_state(
                                session, values.project_id, voice.character_id, lock=True
                            ),
                        }
                    if dialogue is None:
                        raise NotFound("分镜对白不存在")
                    kwargs["trusted_native"] = {"dialogue": dialogue, "voices": voices}
            payload, target = prepare(
                session, owner, values.project_id, item, trusted_version, **kwargs
            )
        target["reference_results"] = list(
            business_image_references(
                session,
                owner,
                values.project_id,
                set(item.reference_media_ids),
                lock=True,
                check_available=False,
            ).values()
        )
        prepared.append((item, payload, target))
    results = []
    for index, (item, payload, target) in enumerate(prepared, 1):
        key = f"business:{receipt_id}:{index}"
        if item.target_kind == "node":
            payload["input"]["metadata"]["client_operation_id"] = key
            summary, _ = CanvasGenerationService(session, settings).create_locked(
                CanvasRuntimeTaskCreate.model_validate(payload),
                key,
            )
            generation_id = summary["id"]
        else:

            def authorized_input(request, *, item=item, target=target, payload=payload):
                request["input"] = deepcopy(payload.get("input") or target["provider_input"])
                if "provider_parameters" in target:
                    request["parameters"] = deepcopy(target["provider_parameters"])
                request["source_snapshot"] = {
                    key: value
                    for key, value in (request.get("source_snapshot") or {}).items()
                    if key
                    in {
                        "asset_row_version",
                        "asset_content_hash",
                        "context_hash",
                        "row_version",
                        "shot_context_hash",
                        "reference_media_id",
                        "reference_layout",
                        "input_mode",
                        "native_speech",
                        "line_hash",
                        "character_id",
                    }
                }
                request.pop("business_intent", None)
                return request

            summary, _ = AIGenerationService(session, settings).create_locked(
                item.media_kind,
                payload,
                key,
                prepare_transform=authorized_input,
                assistant_managed=True,
            )
            generation_id = summary["generation_id"]
        for record in AIGenerationRecordDAO(session).for_task(
            int(generation_id), include_text=False
        ):
            # 来源只由受理边界写入；Worker 以收据和原生任务核验，独立于 Run 生命周期。
            record.config_snapshot = {
                **record.config_snapshot,
                "business_invocation_id": str(receipt_id),
                "business_media": deepcopy(target),
            }
        results.append(
            {
                "generation_id": str(generation_id),
                "target_kind": item.target_kind,
                "target_id": str(item.target_id),
                "count": item.count,
                "status": "accepted",
            }
        )
    return {
        "project_id": str(values.project_id),
        **({"canvas_id": values.canvas_id} if values.canvas_id else {}),
        "admitted": True,
        "settled": False,
        "items": results,
        "result_refs": [
            {
                "kind": "generation",
                "id": item["generation_id"],
                "project_id": str(values.project_id),
            }
            for item in results
        ],
    }


def get_generation_status_locked(session, identity, values, receipt_id, settings):
    from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
    from short_drama.service.assistant.operations.invocation import require_invocation_subject

    dao = BusinessInvocationDAO(session)
    if identity.scope.subject_policy == "selected" and not set(values.generation_ids).issubset(
        identity.scope.generation_ids
    ):
        raise NotFound("生成任务未包含在本次执行范围的受理回执中")
    rows = dao.generations(values.project_id, values.generation_ids)
    if {row.id for row in rows} != set(values.generation_ids):
        raise NotFound("生成任务不存在或超出授权作品")
    if identity.scope.subject_policy == "selected":
        sources = dao.generation_sources(values.generation_ids)
        for row in rows:
            source = sources.get(row.id, {})
            kind, identifier = next(
                (
                    (kind, source[field])
                    for kind, field in (
                        ("canvas_node", "canvas_node"),
                        ("sound_line", "line_id"),
                        ("asset", "asset_id"),
                        ("shot", "shot_id"),
                    )
                    if source.get(field)
                ),
                (None, None),
            )
            if kind is None:
                raise NotFound("生成任务不属于当前已选业务目标")
            require_invocation_subject(identity, kind, identifier)
            if (
                kind == "canvas_node"
                and identity.scope.subject_snapshots.get(f"canvas_node:{identifier}", {}).get(
                    "source_id"
                )
                != source["canvas_id"]
            ):
                raise NotFound("生成任务不属于本次授权画布节点")
    outputs = dao.generation_outputs(values.generation_ids)
    return {
        "project_id": str(values.project_id),
        "settled": all(row.status in {"succeeded", "failed", "cancelled"} for row in rows),
        "items": [
            {
                "generation_id": str(row.id),
                "status": row.status,
                "result_refs": outputs.get(row.id, []),
                "error": {"code": (row.error or {}).get("code")} if row.error else None,
            }
            for row in rows
        ],
    }
