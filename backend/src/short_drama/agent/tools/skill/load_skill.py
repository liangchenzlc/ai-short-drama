from short_drama.agent.skill.loading import load_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import LoadSkill


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
    return load_locked(
        session,
        conversation,
        task,
        operation,
        values,
        settings,
        state=state,
        tool_lookup=tool_lookup,
    )


TOOL = create_runtime_tool(
    "runtime__load_skill",
    "按真实方法 name 与 content_version 加载当前任务所需 SOP，填写用途；重复加载去重。",
    LoadSkill,
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
