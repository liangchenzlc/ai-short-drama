from short_drama.agent.sandbox.resources import ImportResources
from short_drama.agent.tools.sandbox.import_resources_description import DESCRIPTION
from short_drama.agent.tools.sandbox.runtime import resource_spec

SPEC = resource_spec(
    "runtime__import_resources",
    DESCRIPTION,
    ImportResources,
    "import_resources",
    "resources",
)
