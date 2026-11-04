from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按项目剧本身份、正文版本和可选字符选区分段读取 ProjectScript 原稿。",
    when_to_use="用户要求基于已保存原稿改写或引用原文，且当前任务已明确授权该剧本时调用。",
    avoid="不要用它读取 ShotScript 分场正文；不要把读取结果当作已保存的修改。",
    prerequisites=(
        "project_id、script_id 和 content_version "
        "应来自当前授权快照；start/end 必须同时提供且表示半开区间。"
    ),
    effect="只读；offset/limit 按字符分页，不改变正文版本。",
    returns="返回冻结版本的正文片段、实际选区范围和继续读取位置；后续提案必须沿用同一身份与版本。",
    recovery="版本冲突或选区无效时重新读取当前剧本，不要把旧正文用于新版本写入。",
    example='{"project_id":"375104027219726336","script_id":"375104027219726400","content_version":"3","offset":0,"limit":4000}',
).render()
