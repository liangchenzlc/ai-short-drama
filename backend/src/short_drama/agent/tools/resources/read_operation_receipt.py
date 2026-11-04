from short_drama.agent.context.operation_receipts import read_receipt_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.resources.read_operation_receipt_description import DESCRIPTION
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadReceipt


def invoke_locked(
    session,
    conversation,
    task,
    operation,
    values,
    settings,
    *,
    state=None,
    tool_lookup=None,
    resource_prepared=None,
):
    return read_receipt_locked(session, conversation, task, operation, values, state=state)


TOOL = create_runtime_tool(
    "runtime__read_operation_receipt",
    DESCRIPTION,
    ReadReceipt,
    invoke_locked,
    skill_state=True,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="read",
    source="runtime",
    modes=frozenset(set()),
)
