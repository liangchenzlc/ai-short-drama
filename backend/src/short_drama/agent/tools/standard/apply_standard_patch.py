from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.standard.apply_standard_patch_description import DESCRIPTION
from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
from short_drama.service.assistant.operations.standard import apply_standard_patch_locked

SPEC = ToolSpec(
    name="workbench__apply_standard_patch",
    description=DESCRIPTION,
    args_schema=ApplyStandardPatch,
    handler=apply_standard_patch_locked,
    effect="write",
    required_capability="workbench_write",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
