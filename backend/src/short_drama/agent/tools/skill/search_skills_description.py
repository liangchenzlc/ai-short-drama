from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="检索当前模式兼容的创作方法目录和冻结内容版本。",
    when_to_use="任务需要某种创作流程或检查方法，且当前活动方法中没有对应 SOP 时调用。",
    avoid="搜索结果只有目录信息；不要声称已读取方法正文，也不要仅凭相似名称选错版本。",
    prerequisites="query 描述当前任务要解决的具体创作问题；offset/limit 用于目录分页。",
    effect="只读，不会自动激活 Skill 或增加方法正文上下文。",
    returns="返回匹配方法的名称、兼容模式、描述和 content_version。",
    recovery="没有匹配项时换更具体的创作意图查询；加载前选用结果中确切名称和版本。",
    example='{"query":"分场节奏与对白","offset":0,"limit":10}',
).render()
