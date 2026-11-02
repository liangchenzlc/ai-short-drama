from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.script import ReadCurrentScript
from short_drama.service.assistant.operations.script_read import read_current_script_locked

SPEC = ToolSpec(
    name="workbench__read_current_script",
    description="按明确作品、原稿版本及选区分段读取本次授权的项目原稿（ProjectScript）；"
    "不用于读取分场（ShotScript），新建分场无需先调用本工具。",
    args_schema=ReadCurrentScript,
    handler=read_current_script_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
