from short_drama.agent.context.working_set import RecordObservation, record_locked
from short_drama.agent.tools.context.record_task_observation_description import DESCRIPTION
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return record_locked(session, conversation, task, operation, values, settings)


TOOL = create_runtime_tool(
    "runtime__record_task_observation",
    DESCRIPTION,
    RecordObservation,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="context",
    source="runtime",
    modes=frozenset(set()),
)
