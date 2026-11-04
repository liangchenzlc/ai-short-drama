from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.resources.read_image_group_description import DESCRIPTION
from short_drama.schemas.assistant.operations.image import ReadImageGroup
from short_drama.service.assistant.operations.image import read_image_group_business_locked

SPEC = ToolSpec(
    name="workbench__read_image_group",
    description=DESCRIPTION,
    args_schema=ReadImageGroup,
    handler=read_image_group_business_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
