from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.media_generation.submit_generation_batch_description import DESCRIPTION
from short_drama.schemas.assistant.operations.media import SubmitGenerationBatch
from short_drama.service.assistant.operations.media import submit_generation_batch_locked

SPEC = ToolSpec(
    name="workbench__submit_generation_batch",
    description=DESCRIPTION,
    args_schema=SubmitGenerationBatch,
    handler=submit_generation_batch_locked,
    effect="submit",
    required_capability="media_generate",
    expose_mcp=True,
    modes=frozenset(set()),
)
