from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.agent.tools.standard.propose_script_patch_description import DESCRIPTION
from short_drama.schemas.assistant.operations.script import ProposeScriptPatch
from short_drama.service.assistant.operations.script_proposal import propose_locked


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return propose_locked(session, conversation, task, operation, values, settings)


TOOL = create_runtime_tool(
    "runtime__propose_script_patch",
    DESCRIPTION,
    ProposeScriptPatch,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="write",
    required_capability="workbench_write",
    source="runtime",
    modes=frozenset({"standard"}),
)
