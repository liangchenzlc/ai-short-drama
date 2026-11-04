from short_drama.agent.tools.canvas.apply_canvas_patch_description import DESCRIPTION
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.canvas import ApplyCanvasPatch
from short_drama.service.assistant.operations.canvas import apply_canvas_patch_locked

SPEC = ToolSpec(
    name="workbench__apply_canvas_patch",
    description=DESCRIPTION,
    args_schema=ApplyCanvasPatch,
    handler=apply_canvas_patch_locked,
    effect="write",
    required_capability="workbench_write",
    expose_mcp=True,
    modes=frozenset({"infinite_canvas"}),
)
