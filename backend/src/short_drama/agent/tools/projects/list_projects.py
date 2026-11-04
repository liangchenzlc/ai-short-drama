from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.projects.list_projects_description import DESCRIPTION
from short_drama.schemas.assistant.operations.projects import ListProjects
from short_drama.service.assistant.operations.projects import list_projects_locked

SPEC = ToolSpec(
    name="workbench__list_projects",
    description=DESCRIPTION,
    args_schema=ListProjects,
    handler=list_projects_locked,
    expose_mcp=True,
    expose_assistant=False,
    modes=frozenset({"standard", "infinite_canvas"}),
)
