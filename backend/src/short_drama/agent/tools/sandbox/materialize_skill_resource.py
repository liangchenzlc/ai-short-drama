from short_drama.agent.sandbox.resources import MaterializeSkillResource
from short_drama.agent.tools.sandbox.runtime import resource_spec

SPEC = resource_spec(
    "runtime__materialize_skill_resource",
    "把当前 scope 已激活方法的冻结脚本或模板物化到版本独立路径；不会执行，也不会覆盖修改后的文件。",
    MaterializeSkillResource,
    "materialize_skill_resource",
    None,
    skill_state=True,
)
