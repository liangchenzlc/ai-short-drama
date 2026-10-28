"""Source-backed task requirements and deterministic completion evidence checks."""

import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from short_drama.agent.context.subjects import visible_result_reference
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.task.requirements import authoritative_definitions, draft_rows
from short_drama.agent.task.sources import READ_TOOLS as READ_TOOLS
from short_drama.agent.task.sources import WRITE_TOOLS as WRITE_TOOLS
from short_drama.agent.task.sources import source_valid as _source_valid
from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
from short_drama.schemas.base import Identifier, InputModel
from short_drama.utils.json_digest import digest

EvidenceKind = Literal["answer", "read", "write", "image", "skill", "observation", "contract"]
SOURCE_BYTES = 12000


def source_projection(sources: list[dict], restricted: set[int]) -> dict:
    """Keep bounded user text for exact citations after L4 replaces the inputs.

    The original goal and newest corrections take priority. A truncated source
    keeps its sequence so the agent can read the missing original text explicitly.
    """
    eligible = [item for item in sources if int(item["id"]) not in restricted]
    selected, seen, used = [], set(), 0
    for source in [*eligible[:1], *reversed(eligible[-31:])]:
        if source["id"] in seen:
            continue
        raw = source["content"].encode("utf-8")
        content = raw[: min(8000, SOURCE_BYTES - used)].decode("utf-8", errors="ignore")
        item = {"id": source["id"], "seq": source["seq"], "content": content}
        item["truncated"] = content != source["content"]
        cost = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        while content and used + cost > SOURCE_BYTES:
            content = content[: len(content) // 2]
            item.update(content=content, truncated=True)
            cost = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        if not content:
            break
        selected.append(item)
        seen.add(source["id"])
        used += cost
    return {
        "items": sorted(selected, key=lambda item: item["seq"]),
        "omitted_messages": len(eligible) - len(selected),
        "estimated_tokens": (used + 2) // 3,
        "quota_tokens": SOURCE_BYTES // 3,
    }


class Requirement(InputModel):
    key: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str, Field(min_length=1, max_length=300)]
    kind: Annotated[
        EvidenceKind,
        Field(
            description="answer 核对最终答复；read 核对实际读取；write 仅核对保存的业务成果；"
            "image 核对像素交付；skill 核对方法加载；observation 核对当前有来源笔记；"
            "contract 核对已登记交付要求。不同成果类别与数量应分别登记。"
        ),
    ]
    source_message_id: Identifier
    source_quote: Annotated[str, Field(min_length=1, max_length=500)]
    minimum_count: Annotated[int, Field(strict=True, ge=1, le=10000)] = 1
    artifact_kind: Literal["asset", "shot", "canvas_node", "sound_line", "media"] | None = None
    asset_kind: Literal["character", "scene", "prop"] | None = None

    @model_validator(mode="after")
    def meaningful_source(self):
        if not self.source_quote.strip():
            raise ValueError("A requirement must cite nonempty user text")
        if self.asset_kind and (self.kind != "write" or self.artifact_kind != "asset"):
            raise ValueError("Asset kind only applies to saved assets")
        return self


class EvidenceCheck(InputModel):
    satisfied: bool
    evidence_operation_ids: Annotated[list[Identifier], Field(max_length=32)] = Field(
        default_factory=list
    )
    answer_quote: Annotated[str, Field(max_length=2000)] = ""
    reason: Annotated[str, Field(max_length=500)] = ""


class RequirementCheck(Requirement, EvidenceCheck):
    pass


class RegisteredRequirementCheck(EvidenceCheck):
    key: Annotated[str, Field(min_length=1, max_length=64)]


class CompletionAssessment(InputModel):
    intent: Literal["discussion", "execution"]
    checks: Annotated[list[RegisteredRequirementCheck], Field(min_length=1, max_length=32)] = Field(
        description="已有 task_contract 要求仅按 key 提交检查，定义由服务端读取。",
    )

    @model_validator(mode="after")
    def unique_requirements(self):
        keys = [item.key for item in self.checks]
        if len(set(keys)) != len(keys):
            raise ValueError("Review needs 1–32 unique requirement keys")
        return self


class ConfirmRequirements(InputModel):
    """Confirm the drafts the server derived, optionally adding sourced items."""

    keys: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=32)
    ] = Field(default_factory=list)
    additions: Annotated[list[Requirement], Field(max_length=32)] = Field(default_factory=list)
    intent: Literal["discussion", "execution"] = "execution"

    @model_validator(mode="after")
    def meaningful_choice(self):
        if not self.keys and not self.additions:
            raise ValueError("Confirm at least one draft or add a sourced requirement")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("Duplicate draft keys")
        if any(item.key in self.keys for item in self.additions):
            raise ValueError("An addition cannot repeat a confirmed draft key")
        if len({item.key for item in self.additions}) != len(self.additions):
            raise ValueError("Addition keys must be unique")
        return self


def reviewed_projection(contract: dict, floor: int = 0) -> dict:
    definitions = {item["key"]: item for item in contract.get("requirements", [])}
    return {
        key: evidence
        for key, evidence in contract.get("reviewed_evidence", {}).items()
        if key in definitions
        and evidence["definition_hash"] == digest(definitions[key])
        and int(evidence["source_turn_id"]) > floor
    }


def review_tool() -> dict:
    return {
        "name": "submit_completion_review",
        "description": "按已确认要求的 key 核对操作证据和候选答复。",
        "parameters": CompletionAssessment.model_json_schema(),
    }


def _saved_references(row, item: RequirementCheck, scope_keys: set[str] | None) -> set[tuple]:
    assets = {
        str(change["id"]): change["after"].get("kind")
        for change in row.changes or []
        if change.get("after")
    }
    scoped = scope_keys is not None and row.tool_name in {
        "apply_standard_patch",
        "apply_canvas_patch",
    }
    return {
        (ref.get("kind"), str(ref["id"]))
        for ref in row.result_refs
        if ref.get("id")
        and ref.get("status") != "deleted"
        and (not item.artifact_kind or ref.get("kind") == item.artifact_kind)
        and (not item.asset_kind or assets.get(str(ref["id"])) == item.asset_kind)
        and (not scoped or visible_result_reference(ref, scope_keys))
    }


def _evidence_issue(
    item: RequirementCheck, rows: list, floor: int, media: dict, scope_keys: set[str] | None = None
) -> str | None:
    if not rows or any(
        row is None
        or row.status != "succeeded"
        or (row.turn_id <= floor and not (item.kind == "write" and scope_keys is not None))
        or (row.result or {}).get("error")
        for row in rows
    ):
        return "操作证据缺失、失败或已失效"
    if item.kind == "write":
        writes = [row for row in rows if row.tool_name in WRITE_TOOLS]
        if not writes or any(row.tool_name not in WRITE_TOOLS | READ_TOOLS for row in rows):
            return "证据不是实际保存操作"
        # A readback can support semantic verification, but its references must
        # never increase the number of actually saved artifacts.
        refs = set().union(*(_saved_references(row, item, scope_keys) for row in writes))
        return "已保存成果类别或数量不足" if len(refs) < item.minimum_count else None
    if item.kind == "read":
        reads = [row for row in rows if row.tool_name in READ_TOOLS]
        if not reads or any(
            row.tool_name not in READ_TOOLS | {"record_task_observation"} for row in rows
        ):
            return "证据不是实际内容读取操作"
        if any(
            (row.result or {}).get("hydration_intent") and str(row.id) not in media for row in reads
        ):
            return "媒体只有读取意图，缺少实际交付证据"
        # Notes support read coverage but do not count as additional reads.
        rows = reads
    if item.kind == "skill" and any(row.tool_name != "load_skill" for row in rows):
        return "证据不是方法加载操作"
    if item.kind == "image":
        # Directory lookups can support a visual inspection, but only delivered
        # pixel operations count toward its required evidence quantity.
        rows = [row for row in rows if str(row.id) in media]
        if not rows:
            return "图片只有读取意图，缺少实际交付像素的证据"
    if len({str(row.id) for row in rows}) < item.minimum_count:
        return "有效操作证据数量不足"
    return None


def _state_issue(item: RequirementCheck, task, floor: int, requirement_count: int) -> str | None:
    if item.kind == "contract":
        return "已登记交付要求不足" if requirement_count < item.minimum_count else None
    notes = task.effective_state.get("working_set", {}).get("items", [])
    sources = {
        (item["operation_id"], item.get("source_quote", ""))
        for item in notes
        if int(item["turn_id"]) > floor and item.get("kind") == "observation"
    }
    return "当前有来源的观察笔记数量不足" if len(sources) < item.minimum_count else None


def evaluate_review(
    session, conversation, task, run, review: CompletionAssessment, answer: str
) -> dict:
    previous = task.effective_state.get("task_contract", {})
    dao = AgentTranscriptDAO(session)
    restricted, _ = restricted_messages(session, conversation.id)
    applied = {
        str(row.id): row for row in dao.task_input_messages(task.id, task.applied_input_revision)
    }
    definitions = authoritative_definitions(session, task, applied, restricted)
    if draft_rows(session, task) and not definitions:
        return {
            "approved": False,
            "missing": [{"reason": "交付要求尚未确认，请先调用 confirm_requirements"}],
        }
    if any(item.key not in definitions for item in review.checks):
        return {"approved": False, "missing": [{"reason": "checks 必须使用已确认要求的 key"}]}
    checks = [
        RequirementCheck(**definitions[item.key], **item.model_dump(exclude={"key"}))
        for item in review.checks
    ]
    if review.intent == "discussion" and any(item.kind != "answer" for item in checks):
        return {"approved": False, "missing": [{"reason": "讨论验收只能检查答复要求"}]}
    messages = {
        str(row.id): row
        for row in dao.input_messages(
            task.id,
            task.applied_input_revision,
            {int(item.source_message_id) for item in checks},
        )
    }
    evidence_ids = {
        int(identifier) for item in checks for identifier in item.evidence_operation_ids
    }
    operations = {str(row.id): row for row in dao.evidence(task.id, evidence_ids)}
    floor = (run.checkpoint or {}).get("history_floor_turn_id", 0)
    media = task.effective_state.get("media_evidence", {})
    failures, reviewed = [], {}
    for item in checks:
        requirement = definitions[item.key]
        issue = None
        if not _source_valid(item, messages, restricted):
            issue = "用户来源原文不存在、已遗忘或不属于已应用输入"
        elif not item.satisfied:
            issue = item.reason or "用户要求尚未满足"
        elif item.kind in {"observation", "contract"}:
            issue = _state_issue(item, task, floor, len(definitions))
        elif item.kind == "answer":
            if not item.answer_quote.strip() or item.answer_quote not in answer:
                issue = "候选答复未包含所引用的交付内容"
        else:
            rows = [operations.get(str(identifier)) for identifier in item.evidence_operation_ids]
            scope = run.checkpoint.get("subject_scope_keys")
            issue = _evidence_issue(
                item, rows, floor, media, set(scope) if scope is not None else None
            )
            if not issue:
                reviewed[item.key] = {
                    "definition_hash": digest(requirement),
                    "source_turn_id": str(min(row.turn_id for row in rows)),
                    "evidence_operation_ids": [str(value) for value in item.evidence_operation_ids],
                }
        if issue:
            failures.append({"key": item.key, "description": item.description, "reason": issue})
    omitted = set(definitions) - {item.key for item in checks}
    failures.extend(
        {"key": key, "description": definitions[key]["description"], "reason": "遗漏已有交付要求"}
        for key in sorted(omitted)
    )
    if len(definitions) > 32:
        failures.append({"reason": "交付要求超过 32 项，请复用已有 key 并合并同类要求"})
    if len(definitions) <= 32 and all(_source_valid(item, messages, restricted) for item in checks):
        task.effective_state = {
            **task.effective_state,
            "task_contract": {
                "input_revision": str(task.applied_input_revision),
                "intent": review.intent,
                "requirements": list(definitions.values()),
                "reviewed_evidence": {
                    **reviewed_projection(
                        {**previous, "requirements": list(definitions.values())}, floor
                    ),
                    **reviewed,
                },
                "verified": not failures,
            },
        }
    return {"approved": not failures, "missing": failures}
