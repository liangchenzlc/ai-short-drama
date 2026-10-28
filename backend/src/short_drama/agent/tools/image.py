"""Image-group reads and explicit, versioned primary choices for the current task."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import and_, or_, select

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.tools.contracts import IMAGE_CONTENT_TOOL as IMAGE_CONTENT_TOOL
from short_drama.agent.tools.contracts import IMAGE_PRIMARY_TOOL as IMAGE_PRIMARY_TOOL
from short_drama.agent.tools.contracts import IMAGE_READ_TOOL as IMAGE_READ_TOOL
from short_drama.agent.tools.contracts import ReadImageContent as ReadImageContent
from short_drama.agent.tools.contracts import ReadImageGroup as ReadImageGroup
from short_drama.agent.tools.contracts import SetImageGroupPrimary as SetImageGroupPrimary
from short_drama.agent.tools.contracts import SubjectKey as SubjectKey
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.domain import Asset, ImageGroup, MediaFile, Project, ShotScript
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.operations.media import AgentMediaRequest
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput
from short_drama.service.assistant.context.subject import selection_metadata
from short_drama.service.assistant.operations.image import (
    AssistantImageService,
    freeze_image_catalog,
)
from short_drama.utils.json_digest import digest


@dataclass(frozen=True)
class PrimaryDirective:
    selector: str
    output_index: int | None


def parse_primary_directive(content):
    text = content.strip()
    if not text or "\x00" in text or text.endswith(("?", "？")):
        return None
    head = re.split(r"[：:\n]", text, maxsplit=1)[0].strip().rstrip("。")
    head = re.sub(r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", head, count=1)
    head = re.sub(r"^改(?:为|成)\s*", "", head, count=1)
    if re.fullmatch(r"(?:把|将)已选图位(?:设为|设置为|改为)主图", head):
        return PrimaryDirective("image_slot", None)
    match = re.fullmatch(
        r"(?:把|将)\s*(?:(已选图片组|已选素材|已选分镜|图片组\s*ID\s*[1-9][0-9]{0,19})\s*的)?"
        r"\s*第?([0-9一二两三四五六七八九十]{1,3})张(?:设为|设置为|改为)主图",
        head,
    )
    if match is None:
        return None
    number = match[2]
    digits = "零一二三四五六七八九"
    if number.isascii() and number.isdecimal():
        index = int(number)
    elif number == "两":
        index = 2
    elif number in digits:
        index = digits.index(number)
    elif number.count("十") == 1:
        first, last = number.split("十")
        if (first and first not in digits) or (last and last not in digits):
            return None
        index = (digits.index(first) if first else 1) * 10 + (digits.index(last) if last else 0)
    else:
        return None
    if not 1 <= index <= 25:
        return None
    selector = {
        None: "selected",
        "已选图片组": "image_group",
        "已选素材": "asset",
        "已选分镜": "shot",
    }.get(match[1])
    if selector is None:
        selector = re.sub(r"^图片组\s*ID\s*", "", match[1])
    return PrimaryDirective(selector, index)


def image_sources(task, available):
    return [
        {
            "subject_key": subject["subject_key"],
            "group_count": len(subject["image_catalog"]["groups"]),
        }
        for subject in snapshot_subjects(task.context_snapshot)
        if subject["subject_key"] in available and subject.get("image_catalog") is not None
    ]


def generated_image_sources(session, owner, task):
    rows = session.execute(
        select(AgentOperation.id, ImageGroup.id, Project.id)
        .join(AgentMediaRequest, AgentMediaRequest.operation_id == AgentOperation.id)
        .join(ImageGroup, ImageGroup.task_id == AgentMediaRequest.generation_task_id)
        .join(Project, Project.id == ImageGroup.project_id)
        .outerjoin(Asset, Asset.id == ImageGroup.asset_id)
        .outerjoin(ShotScript, ShotScript.id == ImageGroup.shot_id)
        .where(
            AgentOperation.task_id == task.id,
            AgentOperation.tool_name == "submit_generation_batch",
            AgentMediaRequest.task_id == task.id,
            AgentMediaRequest.status.in_(("succeeded", "failed", "conflict")),
            AgentMediaRequest.result.is_not(None),
            Project.owner_user_id == owner,
            Project.workspace_mode == "standard",
            Project.archived_at.is_(None),
            or_(
                and_(Asset.id.is_not(None), Asset.deleted_at.is_(None)),
                and_(ShotScript.id.is_not(None), ShotScript.deleted_at.is_(None)),
            ),
        )
        .order_by(AgentOperation.id, ImageGroup.id)
    ).all()
    return [
        {
            "operation_id": str(operation_id),
            "group_id": str(group_id),
            "source_key": f"{project_id}:{project_id}:image_group:{group_id}",
        }
        for operation_id, group_id, project_id in rows
    ] + _canvas_image_sources(session, owner, task)


def _canvas_image_sources(session, owner, task):
    from short_drama.service.assistant.operations.media_reference import generated_image_references

    rows = session.scalars(
        select(AgentMediaRequest)
        .join(AgentOperation, AgentOperation.id == AgentMediaRequest.operation_id)
        .where(
            AgentMediaRequest.task_id == task.id,
            AgentOperation.task_id == task.id,
            AgentOperation.status.in_(("succeeded", "failed", "conflict")),
            AgentMediaRequest.frozen_target["kind"].as_string() == "node",
            AgentMediaRequest.media_kind == "image",
            AgentMediaRequest.status.in_(("succeeded", "failed", "conflict")),
            AgentMediaRequest.result.is_not(None),
        )
        .order_by(AgentMediaRequest.id)
    ).all()
    identifiers = {
        int(item["media_id"])
        for row in rows
        for item in (row.result or {}).get("items", [])
        if item.get("media_id") and item.get("status") == "ready"
    }
    verified = generated_image_references(session, owner, task.id, identifiers)
    return [
        {
            "operation_id": str(row.operation_id),
            "media_id": str(item["media_id"]),
            "source_key": (
                f"{row.frozen_target['project_id']}:{row.frozen_target['canvas_id']}:"
                f"node:{row.frozen_target['id']}"
            ),
        }
        for row in rows
        for item in (row.result or {}).get("items", [])
        if item.get("status") == "ready"
        and int(item["media_id"]) in verified
        and verified[int(item["media_id"])]["request_id"] == str(row.id)
    ]


def image_result_keys(session, owner, task):
    return {item["source_key"] for item in generated_image_sources(session, owner, task)}


def _generated_catalog(session, conversation, task, values):
    from short_drama.agent.tools.media import generation_status_locked
    from short_drama.schemas.assistant.operations.media import GetGenerationStatus

    receipt = generation_status_locked(
        session, conversation, task, GetGenerationStatus(operation_id=values.operation_id)
    )
    if not receipt["settled"]:
        raise Conflict("生成批次尚未完整结算，请先核对原回执")
    frozen = next(
        (
            item["result"].get("catalog")
            for item in receipt["items"]
            if item["result"] and item["result"].get("image_group_id") == str(values.group_id)
        ),
        None,
    )
    if frozen is None:
        raise WorkflowError("assistant_image_scope", "图片组不在该任务生成回执的冻结结果中", 403)
    current = freeze_image_catalog(
        session, conversation.owner_user_id, receipt["project_id"], "image_group", values.group_id
    )
    media_ids = {
        int(item["media_id"])
        for group in frozen["groups"]
        for item in group["items"]
        if item["media_id"]
    }
    accessible = (
        set(
            session.scalars(
                select(MediaFile.id).where(
                    MediaFile.id.in_(media_ids),
                    MediaFile.project_id == int(receipt["project_id"]),
                    or_(
                        MediaFile.created_by == conversation.owner_user_id,
                        MediaFile.published_at.is_not(None),
                    ),
                )
            )
        )
        if media_ids
        else set()
    )
    if accessible != media_ids:
        raise NotFound("生成图片已删除或不再可访问")
    return (
        frozen,
        current,
        {"operation_id": receipt["operation_id"], "group_id": str(values.group_id)},
    )


def _source_catalog(session, conversation, task, values):
    if values.operation_id is not None:
        frozen, current, source = _generated_catalog(session, conversation, task, values)
        return frozen, current, source, False
    subject = next(
        (
            item
            for item in snapshot_subjects(task.context_snapshot)
            if item["subject_key"] == values.subject_key
        ),
        None,
    )
    if subject is None:
        raise WorkflowError("selection_required", "只能读取本任务主动选入的图片主体", 403)
    frozen = subject.get("image_catalog")
    if frozen is None:
        raise Conflict("此引用没有图片组快照，请主动刷新主体")
    metadata = selection_metadata(session, conversation.owner_user_id, task.context_snapshot)
    status = next(item for item in metadata if item["subject_key"] == values.subject_key)
    if status["availability"] == "unavailable":
        raise NotFound("图片主体已删除或不再可访问")
    current = freeze_image_catalog(
        session, conversation.owner_user_id, subject["project_id"], subject["kind"], subject["id"]
    )
    return (
        frozen,
        current,
        {"subject_key": values.subject_key, "snapshot_id": subject["snapshot_id"]},
        status["availability"] == "changed",
    )


def read_image_group_locked(session, conversation, task, values):
    frozen, current, source, changed = _source_catalog(session, conversation, task, values)
    return _read_catalog(values, frozen, current, source, source_changed=changed)


def _content_source(session, conversation, task, values):
    from short_drama.agent.context.media import MAX_INPUT_BYTES

    if values.group_id is None:
        from short_drama.agent.tools.media import generation_status_locked
        from short_drama.schemas.assistant.operations.media import GetGenerationStatus
        from short_drama.service.assistant.operations.media_reference import (
            generated_image_references,
        )

        receipt = generation_status_locked(
            session, conversation, task, GetGenerationStatus(operation_id=values.operation_id)
        )
        if not receipt["settled"]:
            raise Conflict("生成批次尚未完整结算，请先核对原回执")
        reference = generated_image_references(
            session, conversation.owner_user_id, task.id, {values.media_id}
        ).get(values.media_id)
        intent = session.get(AgentMediaRequest, int(reference["request_id"])) if reference else None
        if intent is None or str(intent.operation_id) != receipt["operation_id"]:
            raise WorkflowError(
                "assistant_image_scope", "图片不在本任务该批次的真实生成结果中", 403
            )
        source, changed = {"operation_id": receipt["operation_id"], **reference}, False
    else:
        source, changed = _group_content_source(session, conversation, task, values)

    media = session.get(MediaFile, values.media_id)
    if media is None or not media.format_code.startswith("image/"):
        raise NotFound("所选图片不存在或类型不受支持")
    if (
        not media.byte_size
        or media.byte_size > MAX_INPUT_BYTES["image"]
        or not media.width
        or not media.height
        or media.width * media.height > 40000000
        or not re.fullmatch(r"[a-f0-9]{64}", media.checksum_sha256 or "")
        or not media.storage_locator.startswith("minio://")
    ):
        raise WorkflowError(
            "assistant_image_unavailable", "图片缺少有效文件信息或超过读取限制，请重新上传", 422
        )
    attachment = {
        "id": f"image:{media.id}",
        "kind": "image",
        "name": f"图片 {media.id}" + (f"（图位 {values.item_id}）" if values.item_id else ""),
        "text_content": None,
        "media_id": str(media.id),
        "storage_locator": media.storage_locator,
        "checksum_sha256": media.checksum_sha256,
    }
    return source, attachment, changed


def _group_content_source(session, conversation, task, values):
    frozen, current, source, changed = _source_catalog(session, conversation, task, values)

    def find_item(catalog):
        return next(
            (
                item
                for group in catalog["groups"]
                if group["id"] == str(values.group_id)
                for item in group["items"]
                if item["id"] == str(values.item_id)
            ),
            None,
        )

    item, current_item = find_item(frozen), find_item(current)
    media_id = str(values.media_id)
    if item is None or media_id not in {item["media_id"], *item["history_media_ids"]}:
        raise WorkflowError("assistant_image_scope", "图片不在所选图位的冻结身份中", 403)
    if current_item is None or media_id not in {
        current_item["media_id"],
        *current_item["history_media_ids"],
    }:
        raise NotFound("所选图片已删除或不再可访问")
    return source, changed or digest(frozen) != digest(current)


def read_image_content_locked(session, conversation, task, operation, values):
    source, attachment, changed = _content_source(session, conversation, task, values)
    record = {
        "source": source,
        "arguments": values.model_dump(mode="json"),
        "attachment": attachment,
    }
    identifier = digest(record)
    run = session.get(AgentRun, operation.run_id)
    checkpoint = deepcopy(run.checkpoint)
    reads = checkpoint.setdefault("image_content_reads", {})
    if identifier not in reads and len(reads) >= 8:
        raise WorkflowError("assistant_image_read_limit", "本次执行已达到图片读取上限", 409)
    reads[identifier] = record
    run.checkpoint = checkpoint
    return {
        **source,
        "image_read_id": identifier,
        **(
            {"group_id": str(values.group_id), "item_id": str(values.item_id)}
            if values.group_id
            else {}
        ),
        "media_id": str(values.media_id),
        "source_changed": changed,
        "delivery": "next_model_input",
    }


def available_image_contents_locked(session, conversation, task, run):
    """Check frozen reads again without fetching bytes or following changed media identities."""
    result = {}
    for identifier, record in (run.checkpoint or {}).get("image_content_reads", {}).items():
        try:
            values = ReadImageContent.model_validate(record["arguments"])
            source, attachment, _changed = _content_source(session, conversation, task, values)
        except (BusinessError, ValueError):
            continue
        if (
            digest(
                {
                    "source": source,
                    "arguments": values.model_dump(mode="json"),
                    "attachment": attachment,
                }
            )
            == identifier
        ):
            result[identifier] = record["attachment"]
    return result


def _read_catalog(values, frozen, current, source, *, source_changed=False):
    current_groups = {item["id"]: item for item in current["groups"]}
    groups = frozen["groups"]
    if values.group_id is not None:
        groups = [item for item in groups if item["id"] == str(values.group_id)]
        if not groups:
            raise WorkflowError("assistant_image_scope", "图片组不在该主体的冻结目录中", 403)
    if values.view == "current":
        groups = [item for item in groups if item["is_current"]]
    elif values.view == "detail" and values.group_id is None and len(groups) != 1:
        raise WorkflowError("selection_required", "请指定冻结目录中的唯一图片组 ID", 403)
    page = deepcopy(groups[values.offset : values.offset + values.limit])
    for group in page:
        if group["id"] not in current_groups:
            raise NotFound("冻结目录中的图片组已删除或不再可访问")
        current_items = {item["id"] for item in current_groups[group["id"]]["items"]}
        if any(item["id"] not in current_items for item in group["items"]):
            raise NotFound("冻结目录中的图位已删除或不再可访问")
        for item in group["items"]:
            if item.get("media_id"):
                item["read_content_args"] = {
                    **{
                        key: source[key] for key in ("subject_key", "operation_id") if key in source
                    },
                    "group_id": group["id"],
                    "item_id": item["id"],
                    "media_id": item["media_id"],
                }
    text = json.dumps(
        {
            "target_kind": frozen["target_kind"],
            "target_id": frozen["target_id"],
            "target_version": frozen["target_version"],
            "groups": page,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    start = min(values.character_offset, len(text))
    content = text[start : start + values.character_limit]
    end = start + len(content)
    continuation = None
    if end < len(text):
        continuation = {"offset": values.offset, "character_offset": end}
    elif values.offset + len(page) < len(groups):
        continuation = {"offset": values.offset + len(page), "character_offset": 0}
    if continuation is not None:
        continuation.update(
            **{
                key: value
                for key, value in source.items()
                if key not in {"snapshot_id", "group_id"}
            },
            view=values.view,
            group_id=str(values.group_id) if values.group_id else None,
            limit=values.limit,
        )
    return {
        **source,
        "source_changed": digest(frozen) != digest(current) or source_changed,
        "content": content,
        "total": len(groups),
        "range": {"offset": start, "length": len(content), "total": len(text)},
        "next": continuation,
    }


def _requests(session, conversation, task, revision):
    restricted, _ = restricted_messages(session, conversation.id)
    rows = session.execute(
        select(AgentMessage, AgentTaskInput)
        .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
        .where(
            AgentMessage.conversation_id == conversation.id,
            AgentMessage.role == "user",
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision <= revision,
        )
        .order_by(AgentTaskInput.input_revision)
    ).all()
    target, snapshot, requests = None, None, []
    for message, item in rows:
        if item.applied_plan_revision is None:
            continue
        target = item.payload.get("execution_target", target)
        snapshot = item.payload.get("context_snapshot", snapshot)
        if message.id in restricted:
            continue
        command = re.sub(
            r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", message.content.strip(), count=1
        )
        if re.match(r"^(?:先)?(?:不要|别|取消|停止|只讨论|先讨论|不再|改为|改成)", command):
            requests = []
        directive = parse_primary_directive(message.content)
        if (
            directive
            and target
            and target.get("mode") == "standard"
            and target == task.execution_target
        ):
            requests.append({"message": message, "snapshot": snapshot, "directive": directive})
    return requests


def _prior(session, task):
    return session.scalars(
        select(AgentOperation)
        .where(
            AgentOperation.task_id == task.id,
            AgentOperation.tool_name.in_({IMAGE_PRIMARY_TOOL, "apply_standard_patch"}),
            AgentOperation.status == "succeeded",
        )
        .order_by(AgentOperation.id)
    ).all()


def _updated_catalog(subject, prior):
    catalog = deepcopy(subject["image_catalog"])
    for operation in prior:
        if (operation.result or {}).get("project_id") != subject["project_id"]:
            continue
        for change in operation.changes:
            if (change["kind"], change["id"]) == (catalog["target_kind"], catalog["target_id"]):
                catalog["target_version"] = str(
                    max(int(catalog["target_version"]), int(change["after"]["row_version"]))
                )
            if change["kind"] == "image_group":
                for group in catalog["groups"]:
                    if group["id"] == change["id"] and int(
                        change["after"]["primary_version"]
                    ) >= int(group["primary_version"]):
                        group.update(
                            {
                                key: change["after"][key]
                                for key in ("is_current", "primary_version", "primary_index")
                            }
                        )
    return catalog


def _choice(task, request, prior):
    original_keys = {item["subject_key"] for item in snapshot_subjects(request["snapshot"])}
    directive = request["directive"]
    options = {}
    for subject in snapshot_subjects(task.context_snapshot):
        if (
            subject["subject_key"] not in original_keys
            or subject["project_id"] != task.execution_target["project_id"]
        ):
            continue
        if (
            directive.selector in {"image_group", "image_slot", "asset", "shot"}
            and subject["kind"] != directive.selector
        ):
            continue
        if not subject.get("image_catalog"):
            continue
        catalog = _updated_catalog(subject, prior)
        for group in catalog["groups"]:
            if directive.selector.isdecimal() and group["id"] != directive.selector:
                continue
            if (
                subject["kind"] in {"asset", "shot"}
                and not directive.selector.isdecimal()
                and not group["is_current"]
            ):
                continue
            for item in group["items"]:
                if (
                    directive.output_index is not None
                    and item["output_index"] != directive.output_index
                ):
                    continue
                if item["status"] != "ready" or not item["media_id"]:
                    continue
                options[(group["id"], item["id"])] = {
                    "subject_key": subject["subject_key"],
                    "group_id": group["id"],
                    "item_id": item["id"],
                    "target_kind": catalog["target_kind"],
                    "target_id": catalog["target_id"],
                    "output_index": item["output_index"],
                    "media_id": item["media_id"],
                    "is_current": group["is_current"],
                    "expected_primary_version": group["primary_version"],
                    "expected_item_version": item["item_version"],
                    "expected_target_version": catalog["target_version"],
                }
    if len(options) != 1:
        raise Conflict("主图对象不唯一、未选入或图片尚未就绪，请明确图片组和图位并主动刷新引用")
    return next(iter(options.values()))


def image_primary_requests_locked(session, conversation, task):
    prior = _prior(session, task)
    result = []
    for request in _requests(session, conversation, task, task.applied_input_revision):
        entry = {
            "request_message_id": str(request["message"].id),
            "request_hash": digest({"role": "user", "content": request["message"].content}),
        }
        completed = next(
            (
                operation
                for operation in prior
                if operation.tool_name == IMAGE_PRIMARY_TOOL
                and str(operation.arguments.get("request_message_id"))
                == entry["request_message_id"]
            ),
            None,
        )
        if completed is not None:
            entry["completed_operation_id"] = (completed.result or {}).get(
                "operation_id", str(completed.id)
            )
            result.append(entry)
            continue
        try:
            entry["choice"] = _choice(task, request, prior)
        except Conflict as error:
            entry["scope_error"] = error.message
        result.append(entry)
    return result


def execute_image_primary_locked(session, conversation, task, operation, values):
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
            "image_primary_request_required", "主图写入必须引用本任务已应用的明确用户指令", 403
        )
    prior = _prior(session, task)
    for previous in prior:
        if previous.tool_name != IMAGE_PRIMARY_TOOL:
            continue
        original = SetImageGroupPrimary.model_validate(previous.arguments)
        if original.request_message_id != values.request_message_id:
            continue
        if (original.group_id, original.item_id) == (values.group_id, values.item_id):
            operation.result_refs = previous.result_refs
            return {**previous.result, "already_applied": True, "operation_id": str(previous.id)}
        raise Conflict("这条主图指令已执行；选择其他图片需要新的明确用户指令")
    choice = _choice(task, request, prior)
    if (values.subject_key, str(values.group_id), str(values.item_id)) != (
        choice["subject_key"],
        choice["group_id"],
        choice["item_id"],
    ):
        raise WorkflowError("assistant_image_scope", "主图对象超出原指令的主动主体和指定图位", 403)
    for field in ("expected_primary_version", "expected_item_version", "expected_target_version"):
        if str(getattr(values, field)) != choice[field]:
            raise Conflict("主图写入版本须来自可信任务目录，请核对引用后重试")
    result = AssistantImageService(session).set_primary_locked(
        conversation.owner_user_id,
        task.execution_target["project_id"],
        values,
        operation_id=operation.id,
        trusted=choice,
    )
    operation.changes = result.pop("changes")
    operation.result_refs = result["result_refs"]
    return {**result, "operation_id": str(operation.id), "already_applied": False}
