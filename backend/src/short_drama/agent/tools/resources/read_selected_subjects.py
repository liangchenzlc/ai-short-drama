from short_drama.agent.runtime.read_tools import _read_selected
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadSelectedSubjects


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return _read_selected(session, conversation, task, values)


TOOL = create_runtime_tool(
    "runtime__read_selected_subjects",
    "按主动选入主体的 subject_key 读取冻结快照，长正文按 next 继续，不递归读取邻居。",
    ReadSelectedSubjects,
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
