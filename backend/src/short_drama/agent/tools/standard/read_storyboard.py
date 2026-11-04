from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.standard.read_storyboard_description import DESCRIPTION
from short_drama.schemas.assistant.operations.projects import ReadProjectCollection
from short_drama.service.assistant.operations.projects import read_storyboard_locked

SPEC = ToolSpec(
    name="workbench__read_storyboard",
    description=DESCRIPTION,
    args_schema=ReadProjectCollection,
    handler=read_storyboard_locked,
    expose_mcp=True,
    expose_assistant=False,
    modes=frozenset({"standard"}),
)
