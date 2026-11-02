from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.image import ReadImageGroup
from short_drama.service.assistant.operations.image import read_image_group_business_locked

SPEC = ToolSpec(
    name="workbench__read_image_group",
    description="按获准作品和目标分页读取图组、图位与历史媒体身份；目录不代表已看见像素。",
    args_schema=ReadImageGroup,
    handler=read_image_group_business_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
