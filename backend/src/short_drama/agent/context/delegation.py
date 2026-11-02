"""Explicit native-task handoffs are declarations, never authorization identities."""

import json
import re
from copy import deepcopy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from short_drama.agent.resources.contracts import ResourceRef
from short_drama.core.exceptions import WorkflowError
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation

HANDOFF = re.compile(r"\n```workbench-inputs\n([^`]+)\n```\s*\Z")


class DelegationInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subjects: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=240)]], Field(max_length=16)
    ] = []
    resources: Annotated[list[ResourceRef], Field(max_length=16)] = []
    generation_ids: Annotated[
        list[Annotated[str, Field(pattern=r"^[1-9][0-9]{0,19}$")]], Field(max_length=16)
    ] = []
    script: bool = False


def parse_inputs(description: str) -> DelegationInputs:
    try:
        if len(description) > 40000:
            raise ValueError("description exceeds 40000 characters")
        if "```workbench-inputs" not in description:
            return DelegationInputs()
        match = HANDOFF.search(description)
        if match is None or description.count("```workbench-inputs") != 1:
            raise ValueError("handoff must be one final fenced JSON block")
        if len(match[1]) > 32768:
            raise ValueError("handoff exceeds 32768 characters")
        return DelegationInputs.model_validate(json.loads(match[1]))
    except (ValueError, ValidationError) as error:
        raise WorkflowError(
            "assistant_delegation_inputs", "子任务输入交接块格式或长度无效", 422
        ) from error


def delegated_inputs_locked(session, run_id, scope_id):
    scope = session.get(AgentExecutionScope, scope_id)
    if scope is None or scope.run_id != run_id:
        raise WorkflowError("assistant_tool_scope", "子任务执行范围不存在", 403)
    if scope.parent_scope_id is None:
        return None
    result = session.scalar(
        select(AgentOperation.result).where(
            AgentOperation.run_id == run_id,
            AgentOperation.tool_call_id == scope.parent_tool_call_id,
            AgentOperation.scope_id == scope.parent_scope_id,
        )
    )
    return deepcopy((result or {}).get("delegation_inputs", {}))


def scope_snapshot_locked(session, task):
    binding = session.info.get("assistant_scope")
    inputs = delegated_inputs_locked(session, *binding) if binding else None
    return inputs.get("context_snapshot") if inputs is not None else task.context_snapshot


def receipt_is_handed_off(inputs, receipt):
    if inputs is None:
        return False
    subjects = {
        f"{'canvas_node' if item['kind'] == 'node' else item['kind']}:{item['id']}"
        for item in (inputs.get("context_snapshot") or {}).get("subjects", [])
    }
    generations = set(inputs.get("generation_ids", []))
    resources = inputs.get("resources", [])
    return bool(receipt.result_refs) and all(
        reference in resources
        or f"{reference.get('kind')}:{reference.get('id')}" in subjects
        or reference.get("kind") == "generation"
        and reference.get("id") in generations
        for reference in receipt.result_refs
    )
