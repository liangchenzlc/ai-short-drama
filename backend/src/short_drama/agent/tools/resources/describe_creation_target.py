from short_drama.agent.runtime.read_tools import _describe
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import DescribeTarget


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return _describe(session, conversation, task, operation, values)


TOOL = create_runtime_tool(
    "runtime__describe_creation_target",
    "读取目标身份、版本、模型能力与原始保存批次目录，不读取作品正文；"
    "按 result_before_id/result_limit 翻页。",
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
