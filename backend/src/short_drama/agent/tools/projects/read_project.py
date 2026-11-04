from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.projects.read_project_description import DESCRIPTION
from short_drama.schemas.assistant.operations.projects import ReadProject
from short_drama.service.assistant.operations.projects import read_project_locked

SPEC = ToolSpec(
    name="workbench__read_project",
    description=DESCRIPTION,
    args_schema=ReadProject,
    handler=read_project_locked,
    expose_mcp=True,
    expose_assistant=False,
    modes=frozenset({"standard", "infinite_canvas"}),
)
