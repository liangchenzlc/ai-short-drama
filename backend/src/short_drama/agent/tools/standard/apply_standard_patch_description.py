from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="以一个原子批次创建或修改标准项目素材、分镜分场和台词。",
    when_to_use="用户明确要求把内容写入作品，且已经读取当前目标版本和所有将被修改的主体时调用。",
    avoid=(
        "不要用它保存 ProjectScript 整篇原稿；create_shot.values.script 保存的是 "
        "ShotScript 分场正文。不要猜测既有对象 ID 或版本。"
    ),
    prerequisites=(
        "先用 runtime__describe_creation_target 取得 storyboard_version；修改既有素材或镜头时 "
        "读取主体和 row_version；对象必须在授权范围内。动作字段名必须为 action。"
    ),
    effect=(
        "成功时原子写入整个 patch；任一动作失败则整批不生效。批内后续动作可用较早 create 动作的 "
        "$key 引用新对象。"
    ),
    returns=(
        "返回 temporary_ids、真实 result_refs、changes 和新 "
        "storyboard_version；只有成功回执表示已保存。"
    ),
    recovery=(
        "字段校验失败时按错误路径修正 action/values；版本冲突时重新读取目标。"
        "状态未知时先查原操作回执，不要重复创建。"
    ),
    example=(
        '{"project_id":"375104027219726336","patch":{"storyboard_version":"1",'
        '"actions":[{"action":"create_shot","key":"scene_01","values":{"title":"第 1 场",'
        '"script":"分场正文","duration_ms":10000}}]}}'
    ),
).render()
