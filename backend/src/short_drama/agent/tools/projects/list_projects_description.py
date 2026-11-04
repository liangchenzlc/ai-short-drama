from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="分页查找当前账号可访问的项目。",
    when_to_use="需要先确定项目身份，或用户尚未指定目标项目时调用。",
    avoid="不要据项目名称自行猜测 ID；返回列表只包含当前账号有权访问的项目。",
    prerequisites="无需项目 ID；query 是可选的名称搜索词。",
    effect="只读，不会创建项目，也不会扩大账号或 MCP 令牌的项目授权。",
    returns="返回项目摘要、项目 ID、模式、总数和分页位置；后续项目工具使用返回的 ID。",
    recovery="空列表表示当前授权范围内没有匹配项目；缩小 query 或检查分页，不要尝试猜 ID。",
    example='{"query":"第一个项目","offset":0,"limit":20}',
).render()
