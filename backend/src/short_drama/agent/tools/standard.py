"""标准结构工具只接受真实用户的有界直接指令，引用与版本由执行端核对。"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.tools.contracts import STANDARD_READ_TOOL as STANDARD_READ_TOOL
from short_drama.agent.tools.contracts import STANDARD_TOOL as STANDARD_TOOL
from short_drama.agent.tools.contracts import ApplyStandardPatch as ApplyStandardPatch
from short_drama.agent.tools.contracts import ReadTaskResults as ReadTaskResults
from short_drama.agent.tools.dialogue import sound_write_scope
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import Asset, Project, ShotScript
from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.service.assistant.operations.dialogue import SOUND_ACTIONS, sound_line_state
from short_drama.service.assistant.operations.standard import AssistantStandardService
from short_drama.utils.json_digest import digest

_KINDS = {
    "角色": "character",
    "场景": "scene",
    "道具": "prop",
    "素材": "asset",
    "分镜": "shot",
    "台词": "sound_line",
}
_FIELDS = {
    "名称": {"name"},
    "标题": {"title"},
    "描述": {"description"},
    "提示词": {"prompt", "video_prompt"},
    "标签": {"tags"},
    "场景时间": {"scene_time"},
    "正文": {"script"},
    "时长": {"duration_ms"},
    "景别": {"shot_size"},
    "图片设置": {"image_settings"},
    "视频设置": {"video_settings"},
}


@dataclass(frozen=True)
class StandardDirective:
    action: str
    limits: dict[str, int]
    kind: str | None = None
    selector: str | None = None
    fields: frozenset[str] | None = None
    associate: bool = False


def _quantity(text: str) -> int:
    if not text:
        return 1
    digits = "零一二三四五六七八九"
    if text.isascii() and text.isdecimal():
        return int(text)
    if text == "两":
        return 2
    if text in digits:
        return digits.index(text)
    if text.count("十") == 1:
        first, last = text.split("十")
        if (not first or first in digits) and (not last or last in digits):
            return (digits.index(first) if first else 1) * 10 + (digits.index(last) if last else 0)
    return 0


def parse_standard_directive(content: str) -> StandardDirective | None:
    # The header grants effects; story text after a colon/newline cannot grant more effects.
    text = content.strip()
    if not text or "\x00" in text or text.endswith(("?", "？")):
        return None
    head = re.split(r"[：:\n]", text, maxsplit=1)[0].strip().rstrip("。")
    head = re.sub(r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", head, count=1)
    head = re.sub(r"^在当前作品(?:中|里)?\s*", "", head, count=1)
    create = re.fullmatch(r"(?:创建|新增|建立)\s*(.+)", head)
    if create:
        body = create[1]
        associate = bool(re.search(r"[，,]?并(?:建立)?关联(?:已选素材|它们)?$", body))
        body = re.sub(r"[，,]?并(?:建立)?关联(?:已选素材|它们)?$", "", body)
        counts: Counter[str] = Counter()
        for part in re.split(r"[、和及与，,]", body):
            match = re.fullmatch(
                r"\s*([0-9一二两三四五六七八九十]{0,3})\s*(?:个|张|条|句)?\s*"
                r"(角色|场景|道具|素材|分镜|台词)(?:卡片|卡)?\s*",
                part,
            )
            if not match or not 1 <= (quantity := _quantity(match[1])) <= 50:
                return None
            counts[_KINDS[match[2]]] += quantity
        if not counts or sum(counts.values()) > 50:
            return None
        return StandardDirective("create", dict(counts), associate=associate)
    if head in {"关联已选分镜和素材", "设置已选分镜的素材关联"}:
        return StandardDirective("set_shot_assets", {}, "shot", "selected", associate=True)
    if head in {"调整已选分镜顺序", "重排已选分镜"}:
        return StandardDirective("reorder_shots", {}, "shot", "selected")
    if head in {"编辑已选分镜的对白", "修改已选分镜的对白"}:
        return StandardDirective("edit_dialogue", {}, "shot", "selected", associate=True)
    sound = re.fullmatch(
        r"(?:修改|更新|编辑)(已选台词|本任务台词|台词\s*ID\s*[a-zA-Z0-9_-]{1,64}?)"
        r"(?:的(正文|角色|音色|配音模型))?",
        head,
    )
    if sound:
        selector = (
            "selected"
            if sound[1] == "已选台词"
            else "results"
            if sound[1] == "本任务台词"
            else re.sub(r"^台词\s*ID\s*", "", sound[1])
        )
        fields = {"正文": "text", "角色": "character", "音色": "voice", "配音模型": "config_id"}
        return StandardDirective(
            "update_sound_line",
            {},
            "sound_line",
            selector,
            frozenset({fields[sound[2]]}) if sound[2] else None,
        )
    match = re.fullmatch(
        r"(修改|更新|编辑|删除)(已选素材|已选分镜|本任务素材|本任务分镜|"
        r"(?:素材|分镜)(?:[“\"][^\n“”\"]+[”\"]|\s*ID\s*[1-9][0-9]{0,19}))"
        r"(?:的(名称|标题|描述|提示词|标签|场景时间|正文|时长|景别|图片设置|视频设置))?",
        head,
    )
    if not match or match[1] == "删除" and match[3]:
        return None
    kind = "asset" if "素材" in match[2] else "shot"
    selector = (
        "selected"
        if match[2].startswith("已选")
        else "results"
        if match[2].startswith("本任务")
        else re.sub(r"^(?:素材|分镜)\s*", "", match[2]).strip('“”"')
    )
    return StandardDirective(
        ("delete_" if match[1] == "删除" else "update_") + kind,
        {},
        kind,
        selector,
        frozenset(_FIELDS[match[3]]) if match[3] else None,
    )


def _rows(session: Session, task: AgentTask, revision: int) -> list:
    return session.execute(
        select(AgentMessage, AgentTaskInput)
        .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
        .where(
            AgentMessage.conversation_id == task.conversation_id,
            AgentMessage.role == "user",
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision <= revision,
        )
        .order_by(AgentTaskInput.input_revision)
    ).all()


def _requests(rows: list, task: AgentTask, restricted: set[int]) -> list[dict[str, Any]]:
    target, snapshot = None, None
    result = []
    for message, item in rows:
        if item.applied_plan_revision is None:
            continue
        target = item.payload.get("execution_target", target)
        snapshot = item.payload.get("context_snapshot", snapshot)
        if message.id in restricted:
            continue
        # An explicit correction invalidates preceding grants; quoted story data does not.
        command = re.sub(
            r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", message.content.strip(), count=1
        )
        if re.match(r"^(?:先)?(?:不要|别|取消|停止|只讨论|先讨论|不再|改为|改成)", command):
            result = []
        directive = parse_standard_directive(message.content)
        if (
            directive
            and target
            and target.get("mode") == "standard"
            and target == task.execution_target
        ):
            result.append(
                {"message": message, "input": item, "directive": directive, "snapshot": snapshot}
            )
    return result


def standard_requests_locked(
    session: Session, conversation: AgentConversation, task: AgentTask
) -> list[dict[str, Any]]:
    restricted, _tasks = restricted_messages(session, conversation.id)
    prior = _successful_operations(session, task)
    result = []
    for item in _requests(_rows(session, task, task.applied_input_revision), task, restricted):
        directive = item["directive"]
        remaining = Counter(directive.limits)
        for previous in prior:
            if str(previous.arguments.get("request_message_id")) == str(item["message"].id):
                if not (previous.result or {}).get("already_applied"):
                    remaining.subtract((previous.result or {}).get("created_counts", {}))
        entry = {
            "request_message_id": str(item["message"].id),
            "request_hash": digest({"role": "user", "content": item["message"].content}),
            "action": directive.action,
            "create_limits": directive.limits,
            "create_remaining": {key: max(0, remaining[key]) for key in directive.limits},
            "kind": directive.kind,
            "selector": directive.selector,
            "fields": sorted(directive.fields) if directive.fields else None,
            "associate": directive.associate,
        }
        try:
            effects = prior + _primary_operations(session, task)
            allowed, versions = (
                ({"asset": [], "shot": []}, {})
                if directive.kind == "sound_line"
                else _scope(task, item, effects)
            )
            for kind, plural in (("asset", "assets"), ("shot", "shots")):
                entry[f"allowed_{plural}"] = [
                    {"id": identifier, "row_version": str(versions[(kind, identifier)])}
                    for identifier in sorted(allowed[kind], key=int)
                ]
            if directive.kind == "sound_line" or directive.limits.get("sound_line"):
                lines, binding = sound_write_scope(task, item, effects)
                entry["sound_revision"] = {
                    key: binding[key]
                    for key in ("row_version", "assembly_version", "timeline_hash")
                }
                entry["allowed_sound_lines"] = sorted(lines)
        except Conflict as error:
            entry["scope_error"] = error.message
        result.append(entry)
    return result


def _successful_operations(session: Session, task: AgentTask) -> list[AgentOperation]:
    return list(
        session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name == STANDARD_TOOL,
                AgentOperation.status == "succeeded",
            )
            .order_by(AgentOperation.id)
        ).all()
    )


def _primary_operations(session: Session, task: AgentTask) -> list[AgentOperation]:
    return list(
        session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name.in_(
                    ("set_image_group_primary", "submit_generation_batch")
                ),
                AgentOperation.status.in_(("succeeded", "failed", "conflict")),
            )
            .order_by(AgentOperation.id)
        ).all()
    )


def standard_result_keys(session: Session, owner: int, task: AgentTask) -> set[str]:
    created = {
        (change["kind"], int(change["id"]))
        for operation in _successful_operations(session, task)
        for change in operation.changes
        if change["before"] is None and change["kind"] in {"asset", "shot"}
    }
    result = set()
    for kind, model in (("asset", Asset), ("shot", ShotScript)):
        identifiers = [identifier for item_kind, identifier in created if item_kind == kind]
        if not identifiers:
            continue
        rows = session.execute(
            select(model.id, model.project_id)
            .join(Project, Project.id == model.project_id)
            .where(
                model.id.in_(identifiers),
                model.deleted_at.is_(None),
                Project.owner_user_id == owner,
                Project.workspace_mode == "standard",
                Project.archived_at.is_(None),
            )
        ).all()
        result.update(f"{project}:{project}:{kind}:{identifier}" for identifier, project in rows)
    for operation in _successful_operations(session, task):
        project_id = (operation.result or {}).get("project_id")
        if (
            project_id is None
            or session.scalar(
                select(Project.id).where(
                    Project.id == int(project_id),
                    Project.owner_user_id == owner,
                    Project.workspace_mode == "standard",
                    Project.archived_at.is_(None),
                )
            )
            is None
        ):
            continue
        for change in operation.changes:
            if change["kind"] != "sound_line" or change["before"] is not None:
                continue
            try:
                sound_line_state(session, project_id, change["id"])
            except NotFound:
                continue
            result.add(f"{project_id}:{project_id}:sound_line:{change['id']}")
    return result


def read_standard_results_locked(
    session: Session, conversation: AgentConversation, task: AgentTask, values: ReadTaskResults
) -> dict[str, Any]:
    """Read saved public fields from task-created or still-selected, task-updated objects."""
    prior = _successful_operations(session, task)
    operation = next((item for item in prior if item.id == values.operation_id), None)
    if operation is None:
        raise NotFound("本任务没有此成功的标准操作回执")
    if (operation.result or {}).get("already_applied"):
        original = (operation.result or {}).get("operation_id")
        operation = next((item for item in prior if str(item.id) == original), None)
        if operation is None:
            raise NotFound("本任务的原始标准操作回执不可用")
    created = {
        (change["kind"], change["id"])
        for item in prior
        for change in item.changes
        if change["before"] is None
    }
    readable = created
    if task.effective_state.get("engine") == ENGINE_VERSION:
        readable = readable | {
            (item["kind"], item["id"])
            for item in snapshot_subjects(task.context_snapshot)
            if item["source_kind"] == "project"
            and item["project_id"] == str((operation.result or {}).get("project_id"))
            and item["kind"] in {"asset", "shot"}
        }
    visible = {(item["kind"], item["id"]) for item in operation.result_refs} & readable
    changes = {
        change["id"]: change
        for change in operation.changes
        if (change["kind"], change["id"]) in visible
    }
    requested = [str(identifier) for identifier in values.ids] or list(changes)
    if len(set(requested)) != len(requested):
        raise WorkflowError("invalid_tool_arguments", "成果引用不能重复", 422)
    if not requested or any(identifier not in changes for identifier in requested):
        raise WorkflowError(
            "assistant_result_scope",
            "请求对象不在该保存批次的可读成果中，请核对原始保存 operation_id 和结果 ID",
            403,
        )
    project_id = (operation.result or {}).get("project_id")
    project = session.scalar(
        select(Project.id).where(
            Project.id == int(project_id),
            Project.owner_user_id == conversation.owner_user_id,
            Project.workspace_mode == "standard",
            Project.archived_at.is_(None),
        )
    )
    if project is None:
        raise NotFound("成果作品已删除或不再可访问")
    result, remaining, continuation = [], values.character_limit, None
    for index, identifier in enumerate(requested):
        change = changes[identifier]
        if change["kind"] == "sound_line":
            _fields, binding = sound_line_state(session, project, identifier)
            current_revision = binding["row_version"]
        else:
            model = Asset if change["kind"] == "asset" else ShotScript
            current_revision = session.scalar(
                select(model.row_version).where(
                    model.id == int(identifier),
                    model.project_id == project,
                    model.deleted_at.is_(None),
                )
            )
        if current_revision is None:
            raise NotFound("成果已删除或不再可访问")
        saved = change["after"]
        source_changed = str(current_revision) != saved["row_version"]
        if change["kind"] == "sound_line":
            source_changed = source_changed or any(
                binding[key] != saved[key]
                for key in ("assembly_version", "timeline_hash", "line_checksum")
            )
        fields = {
            key: value
            for key, value in saved.items()
            if key
            in {
                "name",
                "kind",
                "label",
                "description",
                "prompt",
                "tags",
                "scene_time",
                "state",
                "position",
                "title",
                "script",
                "duration_ms",
                "shot_size",
                "image_settings",
                "video_prompt",
                "video_settings",
                "asset_ids",
                "dialogue",
                "id",
                "character",
                "text",
                "voice",
                "config_id",
                "start_ms",
                "media_id",
                "adopted_hash",
            }
        }
        text = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        offset = values.character_offset if index == 0 else 0
        content = text[offset : offset + remaining]
        end = min(offset + len(content), len(text))
        result.append(
            {
                "id": identifier,
                "kind": change["kind"],
                "project_id": str(project),
                "row_version": saved["row_version"],
                "current_revision": str(current_revision),
                "source_changed": source_changed,
                "content": content,
                "range": {"offset": offset, "length": len(content), "total": len(text)},
            }
        )
        remaining -= len(content)
        if end < len(text) or remaining == 0 and index + 1 < len(requested):
            continuation = {
                "operation_id": str(operation.id),
                "ids": requested[index:] if end < len(text) else requested[index + 1 :],
                "character_offset": end if end < len(text) else 0,
            }
            break
    return {"operation_id": str(operation.id), "results": result, "next": continuation}


def _scope(task: AgentTask, request: dict, prior: list[AgentOperation]) -> tuple[dict, dict]:
    project_id = str(task.execution_target["project_id"])
    current = {
        (item["kind"], item["id"]): item
        for item in snapshot_subjects(task.context_snapshot)
        if item["source_kind"] == "project"
        and item["project_id"] == project_id
        and item["kind"] in {"asset", "shot"}
    }
    selected = {
        (item["kind"], item["id"])
        for item in snapshot_subjects(request["snapshot"])
        if (item["kind"], item["id"]) in current and item["project_id"] == project_id
    }
    created, versions, names = set(), {}, {}
    for key, item in current.items():
        versions[key], names[key] = int(item["revision"]), item["name"]
    for operation in prior:
        if (operation.result or {}).get("project_id") != project_id:
            continue
        for change in operation.changes:
            if change["kind"] not in {"asset", "shot"}:
                continue
            key = change["kind"], change["id"]
            if change["before"] is None:
                created.add(key)
            if key in current or key in created:
                versions[key] = max(versions.get(key, 0), int(change["after"]["row_version"]))
                if key not in names:
                    names[key] = change["after"].get("name", change["after"].get("title", ""))
    directive = request["directive"]
    allowed = selected | created
    if directive.action != "create":
        targets = {key for key in allowed if key[0] == directive.kind}
        if directive.selector == "selected":
            targets &= selected
        elif directive.selector == "results":
            targets &= created
        else:
            targets = {
                key
                for key in targets
                if key[1] == directive.selector.removeprefix("ID").strip()
                or names.get(key) == directive.selector
            }
            if len(targets) != 1:
                raise Conflict("指令对象不唯一或未选入，请明确素材／分镜的名称或 ID")
        if not targets:
            raise Conflict("没有与原指令匹配的已选主体或本任务成果")
        allowed = targets | (
            {key for key in allowed if key[0] == "asset"} if directive.associate else set()
        )
    return {
        kind: [key[1] for key in allowed if key[0] == kind] for kind in ("asset", "shot")
    }, versions


def execute_standard_locked(
    session: Session,
    conversation: AgentConversation,
    task: AgentTask,
    operation: AgentOperation,
    values: ApplyStandardPatch,
    *,
    settings=None,
    scope_request=None,
) -> dict[str, Any]:
    autonomous = scope_request is not None
    request = scope_request
    if not autonomous:
        restricted, _tasks = restricted_messages(session, conversation.id)
        requests = _requests(_rows(session, task, operation.input_revision), task, restricted)
        request = next(
            (item for item in requests if item["message"].id == values.request_message_id), None
        )
        if request is None or values.request_hash != digest(
            {"role": "user", "content": request["message"].content}
        ):
            raise WorkflowError(
                "standard_request_required", "标准写入必须引用本任务已应用的明确用户指令", 403
            )
    prior = _successful_operations(session, task)
    for previous in prior:
        identical = (
            previous.arguments == operation.arguments
            if autonomous
            else ApplyStandardPatch.model_validate(previous.arguments) == values
        )
        if identical:
            operation.result_refs = previous.result_refs
            return {**previous.result, "already_applied": True, "operation_id": str(previous.id)}
    directive = request["directive"]
    effects = prior + _primary_operations(session, task)
    allowed, versions = (
        ({"asset": [], "shot": []}, {})
        if directive.kind == "sound_line"
        else _scope(task, request, effects)
    )
    allowed["sound_line"] = []
    if any(action.action in SOUND_ACTIONS for action in values.patch.actions):
        lines, binding = sound_write_scope(task, request, effects)
        expected = values.patch.sound_revision.model_dump(mode="json")
        if any(binding[key] != expected[key] for key in expected):
            raise Conflict("台词写入须使用原指令目录中的可信声音及画面版本")
        allowed["sound_line"] = sorted(lines)
    remaining = Counter(directive.limits)
    for previous in prior:
        if (
            not autonomous
            and str(previous.arguments.get("request_message_id")) == str(values.request_message_id)
            and not (previous.result or {}).get("already_applied")
        ):
            remaining.subtract((previous.result or {}).get("created_counts", {}))
    created_counts: Counter[str] = Counter()
    touched = set()
    for action in values.patch.actions:
        if action.action.startswith("create_"):
            if not autonomous and directive.action != "create":
                raise WorkflowError("assistant_write_scope", "原指令未授权新增对象", 403)
            kind = (
                action.values.kind
                if action.action == "create_asset"
                else "sound_line"
                if action.action == "create_sound_line"
                else "shot"
            )
            quota_kind = (
                kind
                if autonomous
                or created_counts[kind] < remaining[kind]
                or kind in {"shot", "sound_line"}
                else "asset"
            )
            created_counts[quota_kind] += 1
            if not autonomous and created_counts[quota_kind] > remaining[quota_kind]:
                raise WorkflowError(
                    "assistant_write_quantity", "新增数量超过原用户指令的剩余范围", 403
                )
        elif not autonomous and action.action != directive.action:
            # New objects may be completed inside their creation batch only.
            target = getattr(action, "asset", getattr(action, "shot", getattr(action, "line", "")))
            if not (
                directive.action == "create"
                and target.startswith("$")
                and action.action
                in {
                    "update_asset",
                    "update_shot",
                    "set_shot_assets",
                    "edit_dialogue",
                    "update_sound_line",
                }
            ):
                raise WorkflowError("assistant_write_scope", "工具动作超出原用户指令", 403)
        if action.action == "set_shot_assets" or action.action == "create_shot" and action.assets:
            if not autonomous and not directive.associate:
                raise WorkflowError(
                    "assistant_related_scope", "原指令未授权建立或替换素材关联", 403
                )
        if not autonomous and directive.fields and action.action.startswith("update_"):
            if set(action.patch.model_fields_set) - {"row_version"} - directive.fields:
                raise WorkflowError("assistant_write_scope", "修改字段超出原用户指令", 403)
        if (
            not autonomous
            and directive.action == "create"
            and action.action == "update_asset"
            and "kind" in action.patch.model_fields_set
        ):
            raise WorkflowError("assistant_write_scope", "创建批次不能通过改类型扩大原指令", 403)
        for field, kind in (("asset", "asset"), ("shot", "shot"), ("line", "sound_line")):
            if reference := getattr(action, field, None):
                touched.add((kind, reference))
        touched.update(("asset", ref) for ref in getattr(action, "assets", []))
        touched.update(("shot", ref) for ref in getattr(action, "shots", []))
        if action.action == "edit_dialogue":
            touched.update(("asset", line.character_id) for line in action.document.lines)
    project_id = int(task.execution_target["project_id"])
    project = session.scalar(
        select(Project)
        .where(
            Project.id == project_id,
            Project.owner_user_id == conversation.owner_user_id,
            Project.workspace_mode == "standard",
            Project.archived_at.is_(None),
        )
        .with_for_update()
    )
    if project is None:
        raise NotFound("标准写入目标不存在或无权访问")
    for kind, identifier in sorted(touched):
        if identifier.startswith("$"):
            continue
        if identifier not in allowed[kind]:
            raise WorkflowError(
                "assistant_write_scope", "目标未在原指令的主动主体或本任务成果范围内", 403
            )
        if kind == "sound_line":
            continue
        model = Asset if kind == "asset" else ShotScript
        row = session.scalar(
            select(model)
            .where(
                model.id == int(identifier),
                model.project_id == project_id,
                model.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("标准批次对象已删除或不属于目标作品")
        if row.row_version != versions[(kind, identifier)]:
            raise Conflict("主体在选入或本任务写入后已变化，请核对并主动刷新引用")
    result = AssistantStandardService(session, settings).apply_locked(
        project_id,
        values.patch,
        owner_user_id=conversation.owner_user_id,
        operation_id=operation.id,
        allowed_asset_ids=allowed["asset"],
        allowed_shot_ids=allowed["shot"],
        allowed_sound_line_ids=allowed["sound_line"],
    )
    operation.changes = result.pop("changes")
    visible = {
        (change["kind"], change["id"]) for change in operation.changes if change["before"] is None
    } | {(kind, identifier) for kind, identifiers in allowed.items() for identifier in identifiers}
    result["result_refs"] = [
        item for item in result["result_refs"] if (item["kind"], item["id"]) in visible
    ]
    operation.result_refs = result["result_refs"]
    return {**result, "created_counts": dict(created_counts), "already_applied": False}
