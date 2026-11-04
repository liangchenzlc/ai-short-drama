from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.resources.set_image_group_primary_description import DESCRIPTION
from short_drama.schemas.assistant.operations.image import SetImageGroupPrimary
from short_drama.service.assistant.operations.image import set_image_group_primary_business_locked

SPEC = ToolSpec(
    name="workbench__set_image_group_primary",
    description=DESCRIPTION,
    args_schema=SetImageGroupPrimary,
    handler=set_image_group_primary_business_locked,
    effect="write",
    required_capability="workbench_write",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
