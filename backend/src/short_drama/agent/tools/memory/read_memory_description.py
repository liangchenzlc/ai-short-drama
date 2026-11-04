from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按真实记忆身份和版本读取账号长期记忆正文。",
    when_to_use="目录或搜索结果表明一条记忆相关，需要核对其完整内容和来源时调用。",
    avoid="不要只给 ID 猜测版本；不要把已遗忘来源重新作为当前有效事实。",
    prerequisites=(
        "ids 和 versions 必须来自 memory_catalog/search_memory；offset/character_"
        "limit 用于长正文分页。"
    ),
    effect="只读；版本和遗忘来源过滤由服务端校验，读取计入当前任务字符预算。",
    returns="返回正文、当前版本及来源摘要；后续更新/遗忘/恢复仍需重新依据当前版本操作。",
    recovery="版本不符时重新读取目录获取新版本；分页使用响应中的下一位置。",
    example='{"ids":["375104027219726700"],"versions":{"375104027219726700":"2"},"offset":0,"character_limit":4000}',
).render()
