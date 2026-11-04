from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按关键词检索当前账号的长期记忆。",
    when_to_use="任务可能需要复用用户明确保存的跨会话偏好或事实时调用。",
    avoid="搜索摘要不是可引用的完整来源；不要把相似结果当作已确认事实。",
    prerequisites="query 使用具体的人名、主题或偏好词；limit 限制返回结果数量。",
    effect="只读；只搜索当前账号且记忆功能当前可用的范围。",
    returns="返回匹配记忆的真实 ID、版本、标题、相关片段和读取提示。",
    recovery="需要完整内容时按返回身份调用 read_memory；无结果时不要据此杜撰用户偏好。",
    example='{"query":"偏好的叙事节奏","limit":12}',
).render()
