from short_drama.agent.resources.tool_reads import read_resource_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadResource


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return read_resource_locked(session, conversation, task, operation, values, settings)


TOOL = create_runtime_tool(
    "runtime__read_resource",
    "按附件目录 ref_id 读取本任务附件；媒体填写用途并按需水合。"
    "Skill 文件使用 read_skill_resource。",
    ReadResource,
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
