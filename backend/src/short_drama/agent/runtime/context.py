"""Checkpoint state and process-local dependencies for one trusted execution scope."""

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Annotated, Any

from langchain.agents import AgentState

execution_scope: ContextVar[int | None] = ContextVar("execution_scope", default=None)
model_compaction: ContextVar[list | None] = ContextVar("model_compaction", default=None)
CHILD_EXCLUDED_TOOLS = frozenset(
    {
        "task",
        "runtime__read_conversation_history",
        "runtime__memory_catalog",
        "runtime__search_memory",
        "runtime__read_memory",
        "runtime__remember_memory",
        "runtime__update_memory",
        "runtime__forget_memory",
        "runtime__restore_memory",
    }
)


def merge_activations(current: list[dict], updates: list[dict]) -> list[dict]:
    records = {item["id"]: item for item in current}
    for item in updates:
        previous = records.get(item["id"])
        if previous is not None:
            # 同轮返回顺序不代表事务顺序；同一激活释放后不能被旧快照复活。
            item = {
                **item,
                "active": previous["active"] and item["active"],
                **({"source": "manual"} if previous.get("source") == "manual" else {}),
                **(
                    {"issue": previous["issue"]}
                    if not previous["active"] and previous.get("issue")
                    else {}
                ),
            }
        records[item["id"]] = item
    return list(records.values())


class AssistantState(AgentState, total=False):
    scope_id: str
    applied_input_revision: int
    context_initialized: bool
    skill_activations: Annotated[list[dict], merge_activations]
    selected_skills: list[dict]
    excluded_skill_ids: list[str]
    permitted_tool_names: list[str]
    skill_body_operations: list[str]
    memory_epoch: int
    output_recovery: dict
    tool_failure_streak: dict


@dataclass(frozen=True, kw_only=True, repr=False)
class RuntimeContext:
    owner_id: int
    task_id: int
    run_id: int
    root_scope_id: int
    fence: tuple[int, str]
    factory: Any
    settings: Any
    root_tool_lookup: Callable
    scopes: Any
    waits: Any = None
    questions: Any = None
    business: Any = None
    sandbox: Any = None
    events: Any = None
    # 子图工具池由可信组合边界提供，不能从根 Task 推导。
    root_tool_names: tuple[str, ...] = field(default_factory=tuple)
    subtask_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(3))

    @property
    def scope_id(self) -> int:
        return execution_scope.get() or self.root_scope_id

    @property
    def permitted_tool_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self.root_tool_names
            if self.scope_id == self.root_scope_id or name not in CHILD_EXCLUDED_TOOLS
        )

    def tool_lookup(self, name: str):
        return self.root_tool_lookup(name) if name in self.permitted_tool_names else None
