from short_drama.agent.context.operation_receipts import read_receipt_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadReceipt


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return read_receipt_locked(session, conversation, task, operation, values)


TOOL = create_runtime_tool(
    "runtime__read_operation_receipt",
    "核对当前范围、明确交接的父操作及task已交付的真实子业务收据和撤销收据，不重新执行。",
    ReadReceipt,
    invoke_locked,
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
