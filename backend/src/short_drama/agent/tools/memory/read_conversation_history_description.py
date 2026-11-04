from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按持久消息序号读取当前主会话中仍可见且未被遗忘的历史内容。",
    when_to_use="当前上下文不足以确认用户之前明确提出的要求或当前会话决策时调用。",
    avoid="不要把主会话历史当作长期记忆或授权扩展；不要读取其他会话/子任务历史。",
    prerequisites=(
        "from_seq/to_seq 使用消息 seq，不是 ID；character_offset/character_limit 对返回文本分段。"
    ),
    effect="只读，服务端保留附件可见性、遗忘过滤和当前读取预算。",
    returns="返回消息位置、角色、正文/附件可见性和分页游标。",
    recovery="长内容按返回游标继续；若消息因遗忘/权限过滤而缺失，不要从摘要重建原文。",
    example='{"from_seq":1,"limit":20,"character_offset":0,"character_limit":4000}',
).render()
