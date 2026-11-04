from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.standard.read_current_script_description import DESCRIPTION
from short_drama.schemas.assistant.operations.script import ReadCurrentScript
from short_drama.service.assistant.operations.script_read import read_current_script_locked

SPEC = ToolSpec(
    name="workbench__read_current_script",
    description=DESCRIPTION,
    args_schema=ReadCurrentScript,
    handler=read_current_script_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
