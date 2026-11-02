from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.projects import ListProjects
from short_drama.service.assistant.operations.projects import list_projects_locked

SPEC = ToolSpec(
    name="workbench__list_projects",
    description="分页列出本令牌明确授权且账号当前仍拥有的作品。",
    args_schema=ListProjects,
    handler=list_projects_locked,
    expose_mcp=True,
    expose_assistant=False,
    modes=frozenset({"standard", "infinite_canvas"}),
)
