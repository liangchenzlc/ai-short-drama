from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="核对当前任务的真实目标、版本、写入授权、模型能力和可读取的成果目录。",
    when_to_use="开始作品写入、生成或跨工具交接前，需要确认目标身份或取所需版本/来源游标时调用。",
    avoid="它不读取剧本、素材或分镜正文；不要从摘要文本推测主体 ID 或版本。",
    prerequisites=(
        "无需传目标 ID；目标来自当前任务冻结上下文。model_offset/model_limit "
        "翻模型目录，result_before_id/result_limit 翻保存批次目录。"
    ),
    effect="只读，并按当前授权范围过滤主体、模型及操作回执。",
    returns=(
        "返回当前 target、storyboard_version、canvas_revision、模型能力、write_authorized、"
        "result_sources 和下一页游标。"
    ),
    recovery=(
        "若 target 或 write_authorized 缺失，停止对应写入并核对当前任务目标；下一页使用原响应的 "
        "cursor。"
    ),
    example='{"model_offset":0,"model_limit":10,"result_limit":10}',
).render()
