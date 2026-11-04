from short_drama.agent.sandbox.resources import MaterializeSkillResource
from short_drama.agent.tools.sandbox.materialize_skill_resource_description import DESCRIPTION
from short_drama.agent.tools.sandbox.runtime import resource_spec

SPEC = resource_spec(
    "runtime__materialize_skill_resource",
    DESCRIPTION,
    MaterializeSkillResource,
    "materialize_skill_resource",
    None,
    skill_state=True,
)
