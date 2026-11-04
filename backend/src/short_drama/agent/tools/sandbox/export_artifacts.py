from short_drama.agent.sandbox.resources import ExportArtifacts
from short_drama.agent.tools.sandbox.export_artifacts_description import DESCRIPTION
from short_drama.agent.tools.sandbox.runtime import resource_spec

SPEC = resource_spec(
    "runtime__export_artifacts",
    DESCRIPTION,
    ExportArtifacts,
    "export_artifacts",
    "paths",
)
