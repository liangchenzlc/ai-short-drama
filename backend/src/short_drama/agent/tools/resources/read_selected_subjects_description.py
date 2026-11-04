from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="读取用户明确选入当前任务的主体冻结快照和来源正文。",
    when_to_use="需要核实本任务已选素材/镜头/原稿等主体的字段或来源时调用。",
    avoid="不要读取未选入的邻接对象，也不要用猜测的数据库 ID 扩大范围。",
    prerequisites=(
        "ids 使用 describe_creation_target 或任务上下文中的真实 "
        "subject_key；分页继续沿返回的 next 信息。"
    ),
    effect="只读，主体范围和版本固定为本任务授权快照；不会递归读取邻居。",
    returns="返回主体身份、revision、可读字段及长正文分页游标。",
    recovery="无匹配主体时先核对任务选择；长内容从返回游标继续，不要自行更造 subject_key。",
    example='{"ids":["subject:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"character_offset":0,"character_limit":4000}',
).render()
