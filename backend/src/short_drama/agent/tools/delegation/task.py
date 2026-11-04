"""Directory metadata for the upstream SubAgentMiddleware tool, composed at runtime."""

from deepagents.middleware.subagents import SubAgentMiddleware, TaskToolSchema

from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.delegation.task_description import DESCRIPTION

SPEC = ToolSpec(
    name="task",
    description=DESCRIPTION,
    args_schema=TaskToolSchema,
    handler=SubAgentMiddleware,
    effect="delegate",
    source="runtime",
)
