from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="以有界增量创建、修改、移动、删除画布节点或更新节点连线。",
    when_to_use="用户明确要求修改当前获准无限画布，且已读取对应画布及节点版本时调用。",
    avoid="不要提交整份画布替换文档、猜测媒体 URL，或将标准项目的分镜写入画布工具。",
    prerequisites=(
        "project_id、canvas_id 和 expected_revision 来自当前画布；已有节点同时提供公共 "
        "row_version 和本人 private_revision；新节点用 key，后续引用用 $key。"
    ),
    effect="按 actions 顺序原子应用有界增量；任何动作失败时整批不生效。",
    returns="返回画布新修订、节点 result_refs 和 changes；生成媒体仍需调用独立媒体工具。",
    recovery="版本冲突时重新读取受影响画布和节点；不要以全量文档覆盖并发修改。",
    example='{"project_id":"375104027219726336","canvas_id":"main","patch":{"expected_revision":"4","actions":[{"action":"create_node","key":"note","values":{"type":"text","title":"备注","content":"画面提示"}}]}}',
).render()
