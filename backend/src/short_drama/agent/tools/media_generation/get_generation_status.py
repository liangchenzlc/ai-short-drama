from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.media_generation.get_generation_status_description import DESCRIPTION
from short_drama.schemas.assistant.operations.media import GetGenerationStatus
from short_drama.service.assistant.operations.media import get_generation_status_locked

SPEC = ToolSpec(
    name="workbench__get_generation_status",
    description=DESCRIPTION,
    args_schema=GetGenerationStatus,
    handler=get_generation_status_locked,
    effect="read",
    expose_mcp=True,
    modes=frozenset(set()),
)
