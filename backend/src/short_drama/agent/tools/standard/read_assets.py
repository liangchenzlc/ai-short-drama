from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.projects import ReadProjectCollection
from short_drama.service.assistant.operations.projects import read_assets_locked

SPEC = ToolSpec(
    name="workbench__read_assets",
    description="分页读取获准标准作品的素材及当前版本，写入使用 apply_standard_patch。",
    args_schema=ReadProjectCollection,
    handler=read_assets_locked,
    expose_mcp=True,
    expose_assistant=False,
    modes=frozenset({"standard"}),
)
