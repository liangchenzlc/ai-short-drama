from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.media import GetGenerationStatus
from short_drama.service.assistant.operations.media import get_generation_status_locked

SPEC = ToolSpec(
    name="workbench__get_generation_status",
    description="按作品内真实生成任务身份查询状态和产物引用，不重新提交。",
    args_schema=GetGenerationStatus,
    handler=get_generation_status_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset(set()),
)
