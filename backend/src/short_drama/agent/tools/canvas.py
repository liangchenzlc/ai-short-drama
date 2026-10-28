"""画布增量工具：真实原指令授权、可信版本、累计数量与本任务成果读取。"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.tools.contracts import CANVAS_TOOL as CANVAS_TOOL
from short_drama.agent.tools.contracts import ApplyCanvasPatch as ApplyCanvasPatch
from short_drama.agent.tools.contracts import ReadTaskResults
from short_drama.agent.tools.standard import _quantity, _rows
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import Project, ProjectCanvas
from short_drama.domain.agent import AgentConversation
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.domain.canvas import CanvasNode, CanvasNodeUserState
from short_drama.service.assistant_canvas_service import AssistantCanvasService
from short_drama.utils.json_digest import digest

_KINDS = {"文本": "text", "图片": "image", "视频": "video", "音频": "audio"}
_FIELDS = {
    "名称": {"title"},
    "标题": {"title"},
    "正文": {"content"},
    "提示词": {"prompt"},
    "模型": {"model_config_id"},
    "参数": {"settings"},
    "设置": {"settings"},
}


@dataclass(frozen=True)
class CanvasDirective:
    action: str
    limits: dict[str, int]
    selector: str | None = None
    fields: frozenset[str] | None = None
    connect: bool = False


def parse_canvas_directive(content: str) -> CanvasDirective | None:
    text = content.strip()
    if not text or "\x00" in text or text.endswith(("?", "？")):
        return None
    head = re.split(r"[：:\n]", text, maxsplit=1)[0].strip().rstrip("。")
    head = re.sub(r"^(?:请(?:帮我|你)?|帮我|麻烦你?)\s*", "", head, count=1)
    head = re.sub(r"^(?:改为|改成)\s*", "", head, count=1)
    head = re.sub(r"^在当前画布(?:中|里)?\s*", "", head, count=1)
    match = re.fullmatch(r"(?:创建|新增|建立|搭建)\s*(.+)", head)
    if match:
        body = match[1]
        connect = bool(re.search(r"[，,]?并(?:连线|连接(?:它们|节点)?|建立连线)$", body))
        body = re.sub(r"[，,]?并(?:连线|连接(?:它们|节点)?|建立连线)$", "", body)
        counts: Counter[str] = Counter()
        for part in re.split(r"[、和及与，,]", body):
            item = re.fullmatch(
                r"\s*([0-9一二两三四五六七八九十]{0,3})\s*(?:个|张)?\s*"
                r"(文本|图片|视频|音频)节点\s*",
                part,
            )
            if not item or not 1 <= (quantity := _quantity(item[1])) <= 50:
                return None
            counts[_KINDS[item[2]]] += quantity
        if not counts or sum(counts.values()) > 50:
            return None
        return CanvasDirective("create", dict(counts), connect=connect)
    match = re.fullmatch(r"(连接|连线|断开|解除连接)(已选节点|本任务节点)", head)
    if match:
        return CanvasDirective(
            "connect_nodes" if match[1] in {"连接", "连线"} else "disconnect_nodes",
            {},
            "selected" if match[2] == "已选节点" else "results",
        )
    match = re.fullmatch(
        r"(修改|更新|编辑|移动|删除)(已选节点|本任务节点|"
        r"节点(?:[“\"][^\n“”\"]+[”\"]|\s*ID\s*[\w.-]+))"
        r"(?:的(名称|标题|正文|提示词|模型|参数|设置))?",
        head,
    )
    if not match or match[1] in {"移动", "删除"} and match[3]:
        return None
    selector = (
        "selected"
        if match[2] == "已选节点"
        else "results"
        if match[2] == "本任务节点"
        else re.sub(r"^节点\s*", "", match[2]).strip('“”"')
    )
    return CanvasDirective(
        {"移动": "move_node", "删除": "delete_node"}.get(match[1], "update_node"),
        {},
        selector,
        frozenset(_FIELDS[match[3]]) if match[3] else None,
    )


def _requests(
    session: Session, conversation: AgentConversation, task: AgentTask, revision: int
) -> list[dict[str, Any]]:
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
        if re.match(r"^(?:先)?(?:不要|别|取消|停止|只讨论|先讨论|不再|改为|改成)", command):
            result = []
        directive = parse_canvas_directive(message.content)
        if (
            directive
            and target
            and target.get("mode") == "infinite_canvas"
            and target == task.execution_target
        ):
            result.append({"message": message, "directive": directive, "snapshot": snapshot})
    return result


def _successful_operations(session: Session, task: AgentTask) -> list[AgentOperation]:
    return list(
        session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task.id,
                or_(
                    and_(
                        AgentOperation.tool_name == CANVAS_TOOL,
                        AgentOperation.status == "succeeded",
                    ),
                    and_(
                        AgentOperation.tool_name == "submit_generation_batch",
                        AgentOperation.status.in_({"succeeded", "failed", "conflict"}),
                    ),
                ),
            )
            .order_by(AgentOperation.id)
        )
    )


def _scope(
    task: AgentTask, request: dict, prior: list[AgentOperation]
) -> dict[str, dict[str, str]]:
    target = task.execution_target

    def matches(item):
        return (
            item["source_kind"] == "canvas"
            and item["kind"] == "node"
            and item["project_id"] == target["project_id"]
            and item["source_id"] == target["canvas_id"]
        )

    current = {
        item["id"]: item for item in snapshot_subjects(task.context_snapshot) if matches(item)
    }
    selected = {
        item["id"]
        for item in snapshot_subjects(request["snapshot"])
        if matches(item) and item["id"] in current
    }
    versions = {
        key: item["write_versions"] for key, item in current.items() if item.get("write_versions")
    }
    names = {key: item["name"] for key, item in current.items()}
    created, deleted = set(), set()
    for operation in prior:
        result = operation.result or {}
        if (result.get("project_id"), result.get("canvas_id")) != (
            target["project_id"],
            target["canvas_id"],
        ):
            continue
        for change in operation.changes:
            if change["kind"] != "canvas_node":
                continue
            key, after = change["id"], change["after"]
            if change["before"] is None:
                created.add(key)
            if key in current or key in created:
                version = {field: after[field] for field in ("row_version", "private_revision")}
                existing = versions.get(key)
                if existing is None or all(
                    int(version[field]) >= int(existing[field]) for field in version
                ):
                    versions[key] = version
                names.setdefault(key, after["document"].get("title", ""))
            if after["deleted"]:
                deleted.add(key)
    directive = request["directive"]
    allowed = (selected | created) - deleted
    if directive.action != "create":
        if directive.selector == "selected":
            allowed &= selected
        elif directive.selector == "results":
            allowed &= created
        else:
            allowed = {
                key
                for key in allowed
                if key == directive.selector.removeprefix("ID").strip()
                or names.get(key) == directive.selector
            }
            if len(allowed) != 1:
                raise Conflict("指令节点不唯一或未选入，请明确节点名称或 ID")
        if not allowed:
            raise Conflict("没有与原指令匹配的已选节点或本任务成果")
    if allowed - versions.keys():
        raise Conflict("旧节点引用没有可信私人版本，请核对后主动刷新引用")
    return {key: versions[key] for key in sorted(allowed)}


def _remaining(request: dict, prior: list[AgentOperation]) -> dict[str, int]:
    remaining = Counter(request["directive"].limits)
    for operation in prior:
        if str(operation.arguments.get("request_message_id")) == str(request["message"].id):
            if not (operation.result or {}).get("already_applied"):
                remaining.subtract((operation.result or {}).get("created_counts", {}))
    return {key: max(0, remaining[key]) for key in request["directive"].limits}


def canvas_requests_locked(
    session: Session, conversation: AgentConversation, task: AgentTask
) -> list[dict[str, Any]]:
    prior = _successful_operations(session, task)
    result = []
    for request in _requests(session, conversation, task, task.applied_input_revision):
        directive = request["directive"]
        item = {
            "request_message_id": str(request["message"].id),
            "request_hash": digest({"role": "user", "content": request["message"].content}),
            "action": directive.action,
            "create_limits": directive.limits,
            "create_remaining": _remaining(request, prior),
            "selector": directive.selector,
            "fields": sorted(directive.fields) if directive.fields else None,
            "connect": directive.connect,
        }
        try:
            item["allowed_nodes"] = [
                {"id": key, **version} for key, version in _scope(task, request, prior).items()
            ]
        except Conflict as error:
            item["scope_error"] = error.message
        result.append(item)
    return result


def execute_canvas_locked(
    session: Session,
    conversation: AgentConversation,
    task: AgentTask,
    operation: AgentOperation,
    values: ApplyCanvasPatch,
    *,
    scope_request=None,
) -> dict[str, Any]:
    autonomous = scope_request is not None
    request = scope_request or next(
        (
            item
            for item in _requests(session, conversation, task, operation.input_revision)
            if item["message"].id == values.request_message_id
        ),
        None,
    )
    if not autonomous and (
        request is None
        or values.request_hash != digest({"role": "user", "content": request["message"].content})
    ):
        raise WorkflowError(
            "canvas_request_required", "画布写入必须引用本任务已应用的明确用户指令", 403
        )
    prior = _successful_operations(session, task)
    for previous in prior:
        if previous.tool_name == CANVAS_TOOL and (
            previous.arguments == operation.arguments
            if autonomous
            else ApplyCanvasPatch.model_validate(previous.arguments) == values
        ):
            operation.result_refs = previous.result_refs
            return {**previous.result, "already_applied": True, "operation_id": str(previous.id)}
    directive = request["directive"]
    remaining, counts = ({} if autonomous else _remaining(request, prior)), Counter()
    for action in values.patch.actions:
        if action.action == "create_node":
            if not autonomous and directive.action != "create":
                raise WorkflowError("assistant_write_scope", "原指令未授权新增节点", 403)
            counts[action.values.type] += 1
            if not autonomous and counts[action.values.type] > remaining.get(action.values.type, 0):
                raise WorkflowError(
                    "assistant_write_quantity", "新增节点超过原指令的剩余类型或数量", 403
                )
        elif not autonomous and directive.action == "create":
            if action.action == "connect_nodes" and directive.connect:
                continue
            if action.action == "update_node" and action.node.startswith("$"):
                continue
            raise WorkflowError("assistant_write_scope", "创建指令只允许填写新节点和获准连线", 403)
        elif not autonomous and action.action != directive.action:
            raise WorkflowError("assistant_write_scope", "工具动作超出原用户指令", 403)
        if not autonomous and directive.fields and action.action == "update_node":
            if action.patch.model_fields_set - directive.fields:
                raise WorkflowError("assistant_write_scope", "修改节点字段超出原用户指令", 403)
    target = task.execution_target
    result = AssistantCanvasService(session).apply_locked(
        target["project_id"],
        target["canvas_id"],
        values.patch,
        owner_user_id=conversation.owner_user_id,
        operation_id=operation.id,
        allowed_node_versions=_scope(task, request, prior),
    )
    operation.changes = result.pop("changes")
    operation.result_refs = result["result_refs"]
    return {**result, "created_counts": dict(counts), "already_applied": False}


def canvas_result_keys(session: Session, owner: int, task: AgentTask) -> set[str]:
    created = {
        (
            str((operation.result or {})["project_id"]),
            (operation.result or {})["canvas_id"],
            change["id"],
        )
        for operation in _successful_operations(session, task)
        for change in operation.changes
        if change["kind"] == "canvas_node" and change["before"] is None
    }
    if not created:
        return set()
    rows = session.execute(
        select(CanvasNode.node_key, ProjectCanvas.source_key, Project.id)
        .join(ProjectCanvas, ProjectCanvas.id == CanvasNode.canvas_id)
        .join(Project, Project.id == ProjectCanvas.project_id)
        .where(
            CanvasNode.node_key.in_({key for _project, _canvas, key in created}),
            CanvasNode.archived_at.is_(None),
            ProjectCanvas.archived_at.is_(None),
            Project.archived_at.is_(None),
            Project.workspace_mode == "infinite_canvas",
            Project.owner_user_id == owner,
        )
    ).all()
    return {
        f"{project}:{canvas}:node:{key}"
        for key, canvas, project in rows
        if (str(project), canvas, key) in created
    }


def read_canvas_results_locked(
    session: Session, conversation: AgentConversation, task: AgentTask, values: ReadTaskResults
) -> dict[str, Any]:
    prior = _successful_operations(session, task)
    operation = next((item for item in prior if item.id == values.operation_id), None)
    if operation is not None and (operation.result or {}).get("already_applied"):
        original_id = operation.result.get("operation_id")
        operation = next((item for item in prior if str(item.id) == original_id), None)
    if operation is None:
        raise NotFound("本任务没有此成功的画布操作回执")
    created = {
        (item.result["project_id"], item.result["canvas_id"], change["id"])
        for item in prior
        for change in item.changes
        if change["kind"] == "canvas_node" and change["before"] is None
    }
    target = operation.result
    visible = {
        ref["id"]
        for ref in operation.result_refs
        if ref["kind"] == "canvas_node"
        and (target["project_id"], target["canvas_id"], ref["id"]) in created
    }
    changes = {
        item["id"]: item["after"]
        for item in operation.changes
        if item["kind"] == "canvas_node"
        and item["id"] in visible
        and item["before"] is None
        and not item["after"]["deleted"]
    }
    requested = list(values.ids) or list(changes)
    if len(set(requested)) != len(requested):
        raise WorkflowError("invalid_tool_arguments", "成果引用不能重复", 422)
    if not requested or any(key not in changes for key in requested):
        raise WorkflowError("assistant_result_scope", "只能读取回执中的本任务真实新建节点", 403)
    canvas = session.scalar(
        select(ProjectCanvas)
        .join(Project, Project.id == ProjectCanvas.project_id)
        .where(
            Project.id == int(target["project_id"]),
            Project.owner_user_id == conversation.owner_user_id,
            Project.workspace_mode == "infinite_canvas",
            Project.archived_at.is_(None),
            ProjectCanvas.source_key == target["canvas_id"],
            ProjectCanvas.archived_at.is_(None),
        )
    )
    if canvas is None:
        raise NotFound("成果画布已删除或不再可访问")
    result, remaining, continuation = [], values.character_limit, None
    for index, key in enumerate(requested):
        current = session.execute(
            select(CanvasNode.row_version, CanvasNodeUserState.row_version)
            .outerjoin(
                CanvasNodeUserState,
                (CanvasNodeUserState.canvas_id == CanvasNode.canvas_id)
                & (CanvasNodeUserState.node_key == CanvasNode.node_key)
                & (CanvasNodeUserState.user_id == conversation.owner_user_id),
            )
            .where(
                CanvasNode.canvas_id == canvas.id,
                CanvasNode.node_key == key,
                CanvasNode.archived_at.is_(None),
            )
        ).first()
        if current is None:
            raise NotFound("成果节点已删除或不再可访问")
        saved = changes[key]
        node = saved["document"]
        metadata = node.get("metadata", {})
        fields = {
            "type": node["type"],
            "title": node["title"],
            "position": node["position"],
            "content": metadata.get("content", ""),
            "prompt": metadata.get("prompt", ""),
            "model": metadata.get("model", ""),
            "settings": {
                field: metadata[field]
                for field in (
                    "size",
                    "quality",
                    "seconds",
                    "vquality",
                    "audioVoice",
                    "audioFormat",
                    "audioSpeed",
                    "audioInstructions",
                )
                if field in metadata
            },
        }
        text = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        offset = values.character_offset if index == 0 else 0
        content = text[offset : offset + remaining]
        end = min(offset + len(content), len(text))
        result.append(
            {
                "kind": "canvas_node",
                "id": key,
                "project_id": target["project_id"],
                "canvas_id": target["canvas_id"],
                "row_version": saved["row_version"],
                "private_revision": saved["private_revision"],
                "current_revision": str(current[0]),
                "current_private_revision": str(current[1] or 0),
                "source_changed": (str(current[0]), str(current[1] or 0))
                != (saved["row_version"], saved["private_revision"]),
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
