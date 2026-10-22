"""Explicit task media grants, whole-batch reservations and private native receipts."""

import re
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import select

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.tools.canvas import (
    CanvasDirective,
)
from short_drama.agent.tools.canvas import (
    _scope as canvas_scope,
)
from short_drama.agent.tools.canvas import (
    _successful_operations as canvas_operations,
)
from short_drama.agent.tools.contracts import MEDIA_STATUS_TOOL as MEDIA_STATUS_TOOL
from short_drama.agent.tools.contracts import MEDIA_TOOL as MEDIA_TOOL
from short_drama.agent.tools.dialogue import task_sound_sources
from short_drama.agent.tools.standard import (
    StandardDirective,
    _primary_operations,
    _quantity,
    _rows,
    _scope,
    _successful_operations,
)
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.domain import Asset, Project, ProjectCanvas
from short_drama.domain.agent import AgentRun
from short_drama.domain.agent_media import AgentMediaRequest
from short_drama.domain.agent_task import AgentOperation, AgentTaskControl
from short_drama.domain.canvas import CanvasNode
from short_drama.domain.collaboration import User
from short_drama.schemas.assistant_media import MEDIA_USAGE_KEYS
from short_drama.service.assistant_canvas_media_service import (
    admit_canvas_media_locked,
    prepare_canvas_media_locked,
)
from short_drama.service.assistant_dialogue_service import (
    prepare_dialogue_audio_locked,
)
from short_drama.service.assistant_media_reference_service import generated_image_references
from short_drama.service.assistant_media_service import admit_media_locked, prepare_image_locked
from short_drama.service.assistant_policy_service import policy_state_locked
from short_drama.service.assistant_shot_video_service import prepare_shot_video_locked
from short_drama.service.assistant_voice_service import prepare_voice_design_locked
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


@dataclass(frozen=True)
class MediaDirective:
    kind: str
    selector: str
    count: int
    media_kind: str = "image"
    native_speech: bool = False


def parse_media_directive(content):
    text = content.strip()
    if not text or "\x00" in text or text.endswith(("?", "？")):
        return None
    head = re.split(r"[：:\n]", text, maxsplit=1)[0].strip().rstrip("。")
    head = re.sub(r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", head, count=1)
    speech = re.fullmatch(
        r"(?:为|给)(已选台词|本任务台词|台词\s*ID\s*[a-zA-Z0-9_-]{1,64}?)"
        r"\s*(?:各|每条)?生成(?:([0-9一二两三四五六七八九十]{1,3})段)?配音",
        head,
    )
    if speech:
        if _quantity(speech[2] or "") != 1:
            return None
        selector = (
            "selected"
            if speech[1] == "已选台词"
            else "results"
            if speech[1] == "本任务台词"
            else re.sub(r"^台词\s*ID\s*", "", speech[1])
        )
        return MediaDirective("sound_line", selector, 1, "audio")
    match = re.fullmatch(
        r"(?:为|给)(已选素材|已选分镜|本任务素材|本任务分镜|"
        r"(?:素材|分镜)(?:[“\"][^\n“”\"]+[”\"]|\s*ID\s*[1-9][0-9]{0,19}))"
        r"\s*(?:各|每个)?生成(?:([0-9一二两三四五六七八九十]{1,3})张)?(?:图片|图)",
        head,
    )
    if not match:
        voice = re.fullmatch(
            r"(?:为|给)(已选角色|本任务角色|"
            r"角色(?:[“\"][^\n“”\"]+[”\"]|\s*ID\s*[1-9][0-9]{0,19}))"
            r"\s*(?:各|每个)?生成(?:([0-9一二两三四五六七八九十]{1,3})个)?音色",
            head,
        )
        if voice:
            if _quantity(voice[2] or "") != 1:
                return None
            selector = (
                "selected"
                if voice[1].startswith("已选")
                else "results"
                if voice[1].startswith("本任务")
                else re.sub(r"^角色\s*", "", voice[1]).strip('“”"')
            )
            return MediaDirective("asset", selector, 1, "audio")
        video = re.fullmatch(
            r"(?:为|给)(已选分镜|本任务分镜|"
            r"分镜(?:[“\"][^\n“”\"]+[”\"]|\s*ID\s*[1-9][0-9]{0,19}))"
            r"\s*(?:各|每个)?生成(?:([0-9一二两三四五六七八九十]{1,3})段)?"
            r"(原生有声视频|有声视频|视频)",
            head,
        )
        if video:
            if _quantity(video[2] or "") != 1:
                return None
            selector = (
                "selected"
                if video[1].startswith("已选")
                else "results"
                if video[1].startswith("本任务")
                else re.sub(r"^分镜\s*", "", video[1]).strip('“”"')
            )
            return MediaDirective("shot", selector, 1, "video", video[3] != "视频")
        match = re.fullmatch(
            r"(?:为|给)(已选(?:图片|视频|音频)?节点|本任务(?:图片|视频|音频)?节点|"
            r"节点(?:[“\"][^\n“”\"]+[”\"]|\s*ID\s*[^\s：:]{1,128}))"
            r"(?:各|每个)?生成(?:([0-9一二两三四五六七八九十]{1,3})(?:张|段|个))?"
            r"(图片|视频|音频|结果)",
            head,
        )
        if not match:
            return None
        kinds = {"图片": "image", "视频": "video", "音频": "audio"}
        selected_kind = next((value for key, value in kinds.items() if key in match[1]), None)
        media_kind = kinds.get(match[3], selected_kind)
        count = _quantity(match[2] or "")
        if (
            media_kind is None
            or selected_kind not in {None, media_kind}
            or not 1 <= count <= (4 if media_kind == "image" else 1)
        ):
            return None
        selector = (
            "selected"
            if match[1].startswith("已选")
            else "results"
            if match[1].startswith("本任务")
            else re.sub(r"^节点\s*", "", match[1]).strip('“”"')
        )
        return MediaDirective("node", selector, count, media_kind)
    if not 1 <= (count := _quantity(match[2] or "")) <= 4:
        return None
    selector = (
        "selected"
        if match[1].startswith("已选")
        else "results"
        if match[1].startswith("本任务")
        else re.sub(r"^(?:素材|分镜)\s*", "", match[1]).strip('“”"')
    )
    return MediaDirective("asset" if "素材" in match[1] else "shot", selector, count)


def _requests(session, conversation, task, revision):
    restricted, _ = restricted_messages(session, conversation.id)
    target, snapshot, result = None, None, []
    for message, item in _rows(session, task, revision):
        if item.applied_plan_revision is None:
            continue
        target = item.payload.get("execution_target", target)
        snapshot = item.payload.get("context_snapshot", snapshot)
        if message.id in restricted:
            continue
        command = re.sub(
            r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", message.content.strip(), count=1
        )
        if re.match(r"^(?:先)?(?:不要|别|取消|停止|只讨论|先讨论|不再|改为|改成|只搭建)", command):
            result = []
        directive = parse_media_directive(message.content)
        if (
            directive
            and target
            and target.get("mode")
            == ("infinite_canvas" if directive.kind == "node" else "standard")
            and target == task.execution_target
        ):
            result.append({"message": message, "directive": directive, "snapshot": snapshot})
    return result


def _allowed(session, task, request):
    directive = request["directive"]
    if directive.kind == "sound_line":
        prior = sorted(
            _successful_operations(session, task) + _primary_operations(session, task),
            key=lambda item: item.id,
        )
        bindings, selected, created = task_sound_sources(task, request, prior)
        allowed = (
            selected
            if directive.selector == "selected"
            else created
            if directive.selector == "results"
            else {directive.selector}
        )
        bindings = {key: binding for key, binding in bindings.items() if key in allowed}
        if not bindings:
            raise Conflict("请选入真实台词或指定本任务新建台词，不能从分镜推导配音权限")
        return {"sound_line": set(bindings)}, {
            **{
                ("sound_line", key): int(binding["row_version"])
                for key, binding in bindings.items()
            },
            **{("dialogue_audio", key): binding for key, binding in bindings.items()},
        }
    if directive.kind == "node":
        versions = canvas_scope(
            task,
            {**request, "directive": CanvasDirective("generate", {}, directive.selector)},
            canvas_operations(session, task),
        )
        kinds = set(
            session.scalars(
                select(CanvasNode.node_key)
                .join(ProjectCanvas, ProjectCanvas.id == CanvasNode.canvas_id)
                .where(
                    CanvasNode.project_id == int(task.execution_target["project_id"]),
                    ProjectCanvas.source_key == task.execution_target["canvas_id"],
                    CanvasNode.node_key.in_(versions),
                    CanvasNode.kind == directive.media_kind,
                    CanvasNode.archived_at.is_(None),
                )
            )
        )
        if not kinds:
            raise Conflict("没有匹配类型的获准媒体节点，请明确生成目标")
        return {"node": kinds}, {("node", key): versions[key] for key in kinds}
    prior = _successful_operations(session, task) + _primary_operations(session, task)
    allowed, versions = _scope(
        task,
        {
            **request,
            "directive": StandardDirective("generate", {}, directive.kind, directive.selector),
        },
        prior,
    )
    if directive.media_kind == "audio":
        identifiers = {
            str(identifier)
            for identifier in session.scalars(
                select(Asset.id).where(
                    Asset.id.in_(allowed["asset"]),
                    Asset.project_id == int(task.execution_target["project_id"]),
                    Asset.kind == "character",
                    Asset.deleted_at.is_(None),
                )
            )
        }
        if not identifiers:
            raise Conflict("没有匹配的获准角色，请明确音色生成目标")
        allowed = {"asset": sorted(identifiers, key=int), "shot": []}
        for identifier in identifiers:
            binding = next(
                (
                    item.get("voice_binding")
                    for item in snapshot_subjects(task.context_snapshot)
                    if item["kind"] == "asset"
                    and item["id"] == identifier
                    and item["project_id"] == str(task.execution_target["project_id"])
                ),
                None,
            )
            for operation in prior:
                for change in operation.changes:
                    if change["id"] != identifier:
                        continue
                    if change["kind"] == "asset" and change["before"] is None and binding is None:
                        binding = {"row_version": "0", "record_id": None, "media_id": None}
                    if change["kind"] == "character_voice" and (
                        binding is None
                        or int(change["after"]["row_version"]) > int(binding["row_version"])
                    ):
                        binding = change["after"]
            if binding is None:
                raise Conflict("角色缺少可信音色版本，请主动刷新引用")
            versions[("character_voice", identifier)] = deepcopy(binding)
    if directive.native_speech:
        sources = _native_sources(task, request, prior)
        for identifier in allowed["shot"]:
            shot = sources.get(("shot", identifier), {})
            dialogue = shot.get("fields", {}).get("dialogue")
            if dialogue is None:
                raise Conflict("分镜缺少可信对白快照，请主动刷新引用")
            voices = {}
            for line in dialogue["document"]["lines"]:
                character_id = line["character_id"]
                character = sources.get(("asset", character_id), {})
                binding = character.get("voice_binding")
                if not binding or not (binding["record_id"] and binding["media_id"]):
                    raise Conflict("说话角色及其音色须主动选入或来自本任务真实成果")
                voices[character_id] = {
                    "row_version": character["revision"],
                    "voice_binding": deepcopy(character["voice_binding"]),
                }
            versions[("native_speech", identifier)] = {
                "dialogue": deepcopy(dialogue),
                "voices": voices,
            }
    return allowed, versions


def _native_sources(task, request, prior):
    project_id = str(task.execution_target["project_id"])
    active = {item["subject_key"] for item in snapshot_subjects(task.context_snapshot)}
    selected = {
        (item["kind"], item["id"])
        for item in snapshot_subjects(request["snapshot"])
        if item["subject_key"] in active and item["project_id"] == project_id
    }
    sources = {
        (item["kind"], item["id"]): deepcopy(item)
        for item in snapshot_subjects(task.context_snapshot)
        if (item["kind"], item["id"]) in selected
        and item["project_id"] == project_id
        and item["kind"] in {"asset", "shot"}
    }
    for operation in sorted(prior, key=lambda item: item.id):
        if (operation.result or {}).get("project_id") != project_id:
            continue
        for change in operation.changes:
            key = change["kind"], change["id"]
            if change["kind"] == "character_voice":
                character = sources.get(("asset", change["id"]))
                if character is not None and int(change["after"]["row_version"]) > int(
                    (character.get("voice_binding") or {}).get("row_version", "0")
                ):
                    character["voice_binding"] = deepcopy(change["after"])
            elif change["kind"] in {"asset", "shot"} and (
                key in sources or change["before"] is None
            ):
                source = sources.setdefault(key, {"fields": {}})
                if int(change["after"]["row_version"]) <= int(source.get("revision", "0")):
                    continue
                source["fields"].update(deepcopy(change["after"]))
                source["revision"] = change["after"]["row_version"]
                if change["kind"] == "asset" and change["before"] is None:
                    source["voice_binding"] = {
                        "row_version": "0",
                        "record_id": None,
                        "media_id": None,
                    }
    return sources


def _prior_batches(session, task, request_id):
    return [
        operation
        for operation in session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name == MEDIA_TOOL,
                AgentOperation.arguments["request_message_id"].as_string() == str(request_id),
                AgentOperation.result["admitted"].as_boolean().is_(True),
            )
            .order_by(AgentOperation.id)
        ).all()
        if not (operation.result or {}).get("already_applied")
    ]


def _resumed_after(session, task, previous, run_id=None):
    if previous.finished_at is None:
        return False
    run = (
        session.get(AgentRun, run_id)
        if run_id is not None
        else session.scalar(
            select(AgentRun)
            .where(AgentRun.task_id == task.id)
            .order_by(AgentRun.id.desc())
            .limit(1)
        )
    )
    return (
        run is not None
        and run.task_id == task.id
        and session.scalar(
            select(AgentTaskControl.id).where(
                AgentTaskControl.task_id == task.id,
                AgentTaskControl.kind == "resume",
                AgentTaskControl.source_message_id == run.trigger_message_id,
                AgentTaskControl.applied_at.is_not(None),
                AgentTaskControl.created_at >= previous.finished_at,
            )
        )
        is not None
    )


def _missing_counts(session, previous):
    intents = session.scalars(
        select(AgentMediaRequest).where(AgentMediaRequest.operation_id == previous.id)
    ).all()
    if not intents or any(
        item.status not in {"succeeded", "failed", "conflict", "not_submitted"} for item in intents
    ):
        raise Conflict("原批次结果尚未明确，请先核实原请求")
    return {
        (item.frozen_target["kind"], item.frozen_target["id"]): missing
        for item in intents
        if (
            missing := item.requested_count
            - sum(bool(output.get("media_id")) for output in (item.result or {}).get("items", []))
        )
        > 0
    }


def media_requests_locked(session, conversation, task):
    entries = []
    for request in _requests(session, conversation, task, task.applied_input_revision):
        entry = {
            "request_message_id": str(request["message"].id),
            "request_hash": digest({"role": "user", "content": request["message"].content}),
            "media_kind": request["directive"].media_kind,
            "count_per_target": request["directive"].count,
        }
        try:
            allowed, versions = _allowed(session, task, request)
            entry["targets"] = [
                {
                    "kind": kind,
                    "id": identifier,
                    **(
                        versions[(kind, identifier)]
                        if kind == "node"
                        else {"row_version": str(versions[(kind, identifier)])}
                    ),
                    **(
                        {"voice_version": versions[("character_voice", identifier)]["row_version"]}
                        if request["directive"].media_kind == "audio" and kind == "asset"
                        else {}
                    ),
                    **(
                        {
                            key: versions[("dialogue_audio", identifier)][key]
                            for key in ("assembly_version", "timeline_hash")
                        }
                        if kind == "sound_line"
                        else {}
                    ),
                    **(
                        {
                            "native_dialogue_version": versions[("native_speech", identifier)][
                                "dialogue"
                            ]["row_version"],
                            "native_voice_versions": [
                                {
                                    "character_id": character_id,
                                    "row_version": source["row_version"],
                                    "voice_version": source["voice_binding"]["row_version"],
                                }
                                for character_id, source in versions[("native_speech", identifier)][
                                    "voices"
                                ].items()
                            ],
                        }
                        if request["directive"].native_speech
                        else {}
                    ),
                }
                for kind, identifiers in allowed.items()
                for identifier in sorted(
                    identifiers, key=str if kind in {"node", "sound_line"} else int
                )
            ]
        except Conflict as error:
            entry["scope_error"] = error.message
        prior = _prior_batches(session, task, request["message"].id)
        if prior:
            latest = prior[-1]
            entry["completed_operation_id"] = str(latest.id)
            if _resumed_after(session, task, latest):
                missing = _missing_counts(session, latest)
                if missing:
                    entry["retry_operation_id"] = str(latest.id)
                    entry["retry_targets"] = [
                        {**target, "count": missing[(target["kind"], target["id"])]}
                        for target in entry.get("targets", [])
                        if (target["kind"], target["id"]) in missing
                    ]
        entries.append(entry)
    return entries


def _reference_ids(task, request):
    current = {item["subject_key"] for item in snapshot_subjects(task.context_snapshot)}
    return {
        int(media["media_id"])
        for item in snapshot_subjects(request["snapshot"])
        if item["subject_key"] in current
        for media in item.get("media", [])
        if media.get("kind") == "image"
    }


def _target_kind(item):
    return {"shot_video": "shot", "character_voice": "asset", "dialogue_audio": "sound_line"}.get(
        item.target_kind, item.target_kind
    )


def reserve_batch_locked(session, conversation, task, operation, values, settings):
    request = next(
        (
            item
            for item in _requests(session, conversation, task, operation.input_revision)
            if item["message"].id == values.request_message_id
        ),
        None,
    )
    if request is None or values.request_hash != digest(
        {"role": "user", "content": request["message"].content}
    ):
        raise WorkflowError(
            "assistant_generation_request_required", "生成必须引用本任务已应用的明确用户指令", 403
        )
    prior = _prior_batches(session, task, values.request_message_id)
    previous = next(
        (
            item
            for item in prior
            if item.id != operation.id
            and str(item.arguments.get("retry_operation_id")) == str(values.retry_operation_id)
        ),
        None,
    )
    if previous:
        from short_drama.schemas.assistant_media import SubmitGenerationBatch

        if SubmitGenerationBatch.model_validate(previous.arguments) != values:
            raise Conflict("这次生成已受理，不能更换参数重发；请核对原回执")
        operation.result_refs = deepcopy(previous.result_refs)
        return {
            **deepcopy(previous.result),
            "already_applied": True,
            "operation_id": str(previous.id),
        }
    session.scalar(select(User.id).where(User.id == conversation.owner_user_id).with_for_update())
    if not policy_state_locked(session, conversation.owner_user_id, lock=True)[0]:
        raise WorkflowError("assistant_media_disabled", "助手媒体生成已关闭，未受理生成", 409)
    allowed, versions = _allowed(session, task, request)
    targets = {(kind, identifier) for kind, ids in allowed.items() for identifier in ids}
    counts = dict.fromkeys(targets, request["directive"].count)
    if values.retry_operation_id is not None:
        original = next((item for item in prior if item.id == values.retry_operation_id), None)
        if original is None:
            raise NotFound("本任务没有此生成重试的原始批次")
        if not _resumed_after(session, task, original, operation.run_id):
            raise WorkflowError(
                "assistant_media_resume_required",
                "请在原批次结果明确后显式继续任务，再处理缺失项",
                409,
            )
        counts = _missing_counts(session, original)
        if not counts:
            raise Conflict("原批次没有缺失结果，已保存产物不重复生成")
        if set(counts) - targets:
            raise WorkflowError(
                "assistant_generation_scope", "缺失项已不在当前允许范围，请核对主体", 403
            )
    if {(_target_kind(item), str(item.target_id)) for item in values.requests} != set(counts):
        raise WorkflowError("assistant_generation_scope", "生成批次须匹配原指令的全部明确目标", 403)
    references = _reference_ids(task, request)
    generated_references = generated_image_references(
        session,
        conversation.owner_user_id,
        task.id,
        {identifier for item in values.requests for identifier in item.reference_media_ids}
        - references,
        lock=True,
    )
    prepared = []
    for item in values.requests:
        if item.media_kind != request["directive"].media_kind:
            raise WorkflowError("assistant_generation_scope", "生成类型超出原指令", 403)
        if (
            item.target_kind == "shot_video"
            and (item.native_dialogue_version is not None) != request["directive"].native_speech
        ):
            raise WorkflowError(
                "assistant_generation_scope", "原生有声视频须有明确指令及可信对白／音色来源", 403
            )
        if item.count != counts[(_target_kind(item), str(item.target_id))]:
            raise WorkflowError(
                "assistant_generation_quantity",
                "媒体数量须符合用户指令，未指定时每目标一个结果",
                403,
            )
        if set(item.reference_media_ids) - references - generated_references.keys():
            raise WorkflowError(
                "assistant_generation_reference",
                "参考图必须来自仍选入的明确主体或本任务已保存且可访问的真实产物",
                403,
            )
        prepare = (
            prepare_canvas_media_locked
            if item.target_kind == "node"
            else prepare_shot_video_locked
            if item.target_kind == "shot_video"
            else prepare_voice_design_locked
            if item.target_kind == "character_voice"
            else prepare_dialogue_audio_locked
            if item.target_kind == "dialogue_audio"
            else prepare_image_locked
        )
        prepared.append(
            prepare(
                session,
                conversation.owner_user_id,
                task.execution_target
                if item.target_kind == "node"
                else task.execution_target["project_id"],
                item,
                versions[(_target_kind(item), str(item.target_id))],
                **(
                    {
                        "trusted_voice": versions[("character_voice", str(item.target_id))],
                        "settings": settings,
                    }
                    if item.target_kind == "character_voice"
                    else {
                        "trusted_sound": versions[("dialogue_audio", str(item.target_id))],
                        "settings": settings,
                    }
                    if item.target_kind == "dialogue_audio"
                    else {
                        "trusted_native": versions[("native_speech", str(item.target_id))],
                        "settings": settings,
                    }
                    if request["directive"].native_speech
                    else {}
                ),
            )
        )
        prepared[-1][1]["reference_results"] = [
            generated_references[identifier]
            for identifier in item.reference_media_ids
            if identifier in generated_references
        ]
    quantity = sum(item.count for item in values.requests)
    meter_key = MEDIA_USAGE_KEYS[request["directive"].media_kind]
    usage = deepcopy(task.media_usage)
    meter = usage.setdefault(meter_key, {"reserved": 0, "submitted": 0, "succeeded": 0})
    limit = task.media_limits.get(meter_key)
    if limit is not None and meter["reserved"] + meter["submitted"] + quantity > limit:
        raise WorkflowError(
            "assistant_media_budget_exhausted",
            "剩余媒体数量不足以受理整个批次，请调整上限后明确继续",
            409,
        )
    meter["reserved"] += quantity
    task.media_usage = usage
    # Freeze all intents and reserve the whole batch before native admission.
    intents = []
    for index, (item, (payload, target)) in enumerate(
        zip(values.requests, prepared, strict=True), 1
    ):
        intent = AgentMediaRequest(
            id=next_id(),
            task_id=task.id,
            operation_id=operation.id,
            item_index=index,
            media_kind=item.media_kind,
            requested_count=item.count,
            frozen_target=target,
            frozen_request=payload,
            reservation_state="reserved",
            status="reserved",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(intent)
        intents.append(intent)
    session.flush()
    for intent in intents:
        try:
            with session.begin_nested():
                (
                    admit_canvas_media_locked
                    if intent.frozen_target["kind"] == "node"
                    else admit_media_locked
                )(session, conversation.owner_user_id, intent, settings)
        except BusinessError as error:
            intent.status, intent.reservation_state = "not_submitted", "released"
            intent.error = {"code": error.code, "message": error.message}
            usage = deepcopy(task.media_usage)
            usage[meter_key]["reserved"] -= intent.requested_count
            task.media_usage = usage
        intent.updated_at = utcnow()
    session.flush()
    return batch_receipt_locked(session, task, operation)


def batch_receipt_locked(session, task, operation):
    intents = session.scalars(
        select(AgentMediaRequest)
        .where(
            AgentMediaRequest.task_id == task.id,
            AgentMediaRequest.operation_id == operation.id,
        )
        .order_by(AgentMediaRequest.item_index)
    ).all()
    if not intents:
        raise WorkflowError(
            "assistant_media_receipt_missing", "媒体批次账本缺失，不能确认完成", 409
        )
    return {
        "operation_id": str(operation.id),
        "project_id": (task.execution_target or {}).get("project_id"),
        **(
            {"canvas_id": task.execution_target["canvas_id"]}
            if (task.execution_target or {}).get("mode") == "infinite_canvas"
            else {}
        ),
        "admitted": True,
        "settled": all(
            item.status in {"succeeded", "failed", "conflict", "not_submitted"} for item in intents
        ),
        "items": [
            {
                "request_id": str(item.id),
                "item_index": item.item_index,
                "media_kind": item.media_kind,
                "target_kind": item.frozen_target["kind"],
                "target_id": item.frozen_target["id"],
                "generation_id": str(item.generation_task_id) if item.generation_task_id else None,
                "count": item.requested_count,
                "status": item.status,
                "reservation_state": item.reservation_state,
                "result": item.result,
                "error": item.error,
            }
            for item in intents
        ],
    }


def generation_status_locked(session, conversation, task, values):
    operation = session.scalar(
        select(AgentOperation).where(
            AgentOperation.id == values.operation_id,
            AgentOperation.task_id == task.id,
            AgentOperation.tool_name == MEDIA_TOOL,
        )
    )
    if operation is None:
        raise NotFound("本任务中没有此媒体批次")
    if (operation.result or {}).get("already_applied"):
        original_id = operation.result.get("operation_id")
        original = (
            session.scalar(
                select(AgentOperation).where(
                    AgentOperation.id == int(original_id),
                    AgentOperation.task_id == task.id,
                    AgentOperation.tool_name == MEDIA_TOOL,
                )
            )
            if original_id
            else None
        )
        if original is None or original.id == operation.id:
            raise NotFound("媒体批次原始回执不存在")
        operation = original
    project_id = (operation.result or {}).get("project_id")
    if (
        project_id is None
        or session.scalar(
            select(Project.id).where(
                Project.id == int(project_id),
                Project.owner_user_id == conversation.owner_user_id,
                Project.archived_at.is_(None),
            )
        )
        is None
    ):
        raise NotFound("媒体批次作品已删除或不再可访问")
    return batch_receipt_locked(session, task, operation)
