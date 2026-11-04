from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="把跨上下文压缩仍需要的简短事实记入当前任务工作集，并保留原始证据来源。",
    when_to_use="已读取的事实可能在后续摘要/压缩中丢失，且该事实确实影响未完成目标时调用。",
    avoid="不要记录计划、推测或尚未验证的助手陈述；图片观察必须先通过工具实际读取像素。",
    prerequisites=(
        "operation_id 来自本任务真实成功读取操作；source_quote 必须逐字存在于其原始 "
        "content；note 只概括可验证观察。"
    ),
    effect="写入当前任务上下文记忆，不写入账号长期记忆或项目作品。",
    returns="返回已记录观察及来源消息/操作身份。",
    recovery="原句校验失败时回到来源操作重新读取并引用连续原文，不要改写成伪引文。",
    example='{"operation_id":"375104027219726700","kind":"fact","source_quote":"正文中的原始连续短句","note":"主角在第一场已拿到录音。"}',
).render()
