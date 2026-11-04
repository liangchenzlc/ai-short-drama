from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.resources.read_image_content_description import DESCRIPTION
from short_drama.schemas.assistant.operations.image import ReadImageContent
from short_drama.service.assistant.operations.image import read_image_content_business_locked

SPEC = ToolSpec(
    name="workbench__read_image_content",
    description=DESCRIPTION,
    args_schema=ReadImageContent,
    handler=read_image_content_business_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset({"standard", "infinite_canvas"}),
)
