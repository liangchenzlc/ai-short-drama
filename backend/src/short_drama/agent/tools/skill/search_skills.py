from short_drama.agent.skill.contracts import SearchSkills
from short_drama.agent.skill.loading import search_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool


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
    return search_locked(session, conversation, task, operation, values, settings, state=state)


TOOL = create_runtime_tool(
    "runtime__search_skills",
    "搜索当前模式兼容方法的名称、描述和冻结版本，不自动加载正文。",
    SearchSkills,
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
