from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.canvas import ApplyCanvasPatch
from short_drama.service.assistant.operations.canvas import apply_canvas_patch_locked

SPEC = ToolSpec(
    name="workbench__apply_canvas_patch",
    description="按画布修订和节点公共、私人版本保存有界增量，临时引用使用 $key。",
    args_schema=ApplyCanvasPatch,
    handler=apply_canvas_patch_locked,
    effect="write",
    expose_mcp=True,
    modes=frozenset({"infinite_canvas"}),
)
