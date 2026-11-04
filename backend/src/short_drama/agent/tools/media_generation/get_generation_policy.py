from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.media_generation.get_generation_policy_description import DESCRIPTION
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.tasks.policy import ReadGenerationPolicy
from short_drama.service.assistant.tasks.policy import generation_policy_locked


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return generation_policy_locked(session, conversation.owner_user_id).model_dump(mode="json")


TOOL = create_runtime_tool(
    "runtime__get_generation_policy",
    DESCRIPTION,
    ReadGenerationPolicy,
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
