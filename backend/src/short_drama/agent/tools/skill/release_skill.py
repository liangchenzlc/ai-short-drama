from short_drama.agent.skill.progressive import release_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.agent.tools.skill.release_skill_description import DESCRIPTION
from short_drama.schemas.assistant.operations.tool_inputs import ReleaseSkill


def invoke_locked(
    session,
    conversation,
    task,
    operation,
    values,
    settings,
    *,
    state,
    tool_lookup,
    resource_prepared=None,
):
    return release_locked(session, conversation, task, operation, values, settings, state=state)


TOOL = create_runtime_tool(
    "runtime__release_skill",
    DESCRIPTION,
    ReleaseSkill,
    invoke_locked,
    skill_state=True,
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
