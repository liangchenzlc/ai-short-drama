from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="读取标准项目素材/分镜目标的图片组、图位、历史媒体身份和并发版本。",
    when_to_use="需要挑选、核对或采用真实生成图片前，先读取对应目标的图组目录。",
    avoid="不要用它读取像素或确认图片已看过；需要观察图像时调用 read_image_content。",
    prerequisites=(
        "project_id 与 target_kind/target_id 来自当前任务授权主体；group_id "
        "可选，用于限定某个真实图组。"
    ),
    effect=(
        "只读，view=current/detail/history 选择返回范围；offset/limit "
        "分页图组，character_offset/character_limit 分页 JSON 正文。"
    ),
    returns=(
        "返回图组 primary_version、图位 item_version、目标 "
        "target_version、就绪状态和下一步 read_content_args；不返回像素。"
    ),
    recovery=(
        "图片状态为 generating/failed "
        "时等待或查询原任务；采用主图时必须沿返回的图组、图位及目标版"
        "本。"
    ),
    example='{"project_id":"375104027219726336","target_kind":"shot","target_id":"375104027219726500","view":"current","offset":0,"limit":5}',
).render()
