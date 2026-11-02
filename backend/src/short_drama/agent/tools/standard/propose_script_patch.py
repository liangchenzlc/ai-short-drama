from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.script import ProposeScriptPatch
from short_drama.service.assistant.operations.script_proposal import propose_locked


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return propose_locked(session, conversation, task, operation, values, settings)


TOOL = create_runtime_tool(
    "runtime__propose_script_patch",
    "根据刚读取的剧本身份、版本及引用选区保存修改建议；当前正文不变，用户须在前端审阅后显式采用。",
    ProposeScriptPatch,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="write",
    source="runtime",
    modes=frozenset({"standard"}),
)
