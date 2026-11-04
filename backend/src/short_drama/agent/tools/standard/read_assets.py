from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.standard.read_assets_description import DESCRIPTION
from short_drama.schemas.assistant.operations.projects import ReadProjectCollection
from short_drama.service.assistant.operations.projects import read_assets_locked

SPEC = ToolSpec(
    name="workbench__read_assets",
    description=DESCRIPTION,
    args_schema=ReadProjectCollection,
    handler=read_assets_locked,
    expose_mcp=True,
    expose_assistant=False,
    modes=frozenset({"standard"}),
)
