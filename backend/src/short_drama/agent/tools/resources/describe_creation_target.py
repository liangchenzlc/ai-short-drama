from short_drama.agent.runtime.read_tools import _describe
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.resources.describe_creation_target_description import DESCRIPTION
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import DescribeTarget


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return _describe(session, conversation, task, operation, values)


TOOL = create_runtime_tool(
    "runtime__describe_creation_target",
    DESCRIPTION,
    DescribeTarget,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="read",
    source="runtime",
    modes=frozenset(set()),
)
