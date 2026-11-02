from short_drama.agent.skill.contracts import ReadSkillResource
from short_drama.agent.skill.resource_reads import (
    prepare_skill_resource,
    read_skill_resource_locked,
)
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
    return read_skill_resource_locked(
        session,
        conversation,
        task,
        operation,
        values,
        settings,
        state=state,
        tool_lookup=tool_lookup,
        resource_prepared=resource_prepared,
    )


TOOL = create_runtime_tool(
    "runtime__read_skill_resource",
    "按当前激活方法的 activation_id 和冻结 manifest 相对路径读取资料、模板或图片，不执行文件。",
    ReadSkillResource,
    invoke_locked,
    prepare=prepare_skill_resource,
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
