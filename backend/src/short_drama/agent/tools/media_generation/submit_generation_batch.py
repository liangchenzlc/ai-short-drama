from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.media import SubmitGenerationBatch
from short_drama.service.assistant.operations.media import submit_generation_batch_locked

SPEC = ToolSpec(
    name="workbench__submit_generation_batch",
    description="原子受理有界媒体批次并立即返回原生异步任务身份；受理不等于完成，沿原任务查询结果。",
    args_schema=SubmitGenerationBatch,
    handler=submit_generation_batch_locked,
    effect="submit",
    expose_mcp=True,
    modes=frozenset(set()),
)
