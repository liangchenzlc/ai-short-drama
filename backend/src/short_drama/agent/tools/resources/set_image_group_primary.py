from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.image import SetImageGroupPrimary
from short_drama.service.assistant.operations.image import set_image_group_primary_business_locked

SPEC = ToolSpec(
    name="workbench__set_image_group_primary",
    description="采用真实图组和图位为主图，核对目标、图组和图位三类预期版本。",
    args_schema=SetImageGroupPrimary,
    handler=set_image_group_primary_business_locked,
    effect="write",
    required_capability="workbench_write",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
