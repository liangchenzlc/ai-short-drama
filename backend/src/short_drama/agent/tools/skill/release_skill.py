from short_drama.agent.skill.progressive import release_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
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
    "阶段完成后释放当前任务自动方法正文；手选方法由用户管理，保留历史回执。",
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
