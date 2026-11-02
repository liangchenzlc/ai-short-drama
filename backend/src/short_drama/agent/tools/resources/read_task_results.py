from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.results import read_results_locked
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadTaskResults


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return read_results_locked(session, conversation, task, operation, values, settings)


TOOL = create_runtime_tool(
    "runtime__read_task_results",
    "按原始保存批次 operation_id 回读本任务白名单成果正文；长结果按 next 继续。",
    ReadTaskResults,
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
