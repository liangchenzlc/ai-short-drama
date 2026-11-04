from short_drama.agent.runtime.read_tools import _read_selected
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.resources.read_selected_subjects_description import DESCRIPTION
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadSelectedSubjects


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return _read_selected(session, conversation, task, values)


TOOL = create_runtime_tool(
    "runtime__read_selected_subjects",
    DESCRIPTION,
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
