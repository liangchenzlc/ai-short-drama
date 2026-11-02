from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.image import ReadImageContent
from short_drama.service.assistant.operations.image import read_image_content_business_locked

SPEC = ToolSpec(
    name="workbench__read_image_content",
    description=(
        "按获准素材/分镜的图组图位或画布节点及媒体身份读取图片内容引用；"
        "填写用途，可裁剪或提高 detail。"
    ),
    args_schema=ReadImageContent,
    handler=read_image_content_business_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset({"standard", "infinite_canvas"}),
)
