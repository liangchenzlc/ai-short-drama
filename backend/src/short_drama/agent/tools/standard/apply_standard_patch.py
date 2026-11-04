from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
from short_drama.service.assistant.operations.standard import apply_standard_patch_locked

SPEC = ToolSpec(
    name="workbench__apply_standard_patch",
    description="按真实作品版本原子创建或修改素材、分镜分场及台词；"
    "create_shot 的 values.script 保存分场（ShotScript）正文，"
    "无原稿引用也可在授权目标新建分场。"
    "先用 runtime__describe_creation_target 取得 patch.storyboard_version；"
    "关联或修改既有对象仍须对象授权，批内临时引用使用 $key。",
    args_schema=ApplyStandardPatch,
    handler=apply_standard_patch_locked,
    effect="write",
    required_capability="workbench_write",
    expose_mcp=True,
    modes=frozenset({"standard"}),
)
