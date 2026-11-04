from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="更新一条现有账号长期记忆，并关联新的真实用户来源。",
    when_to_use="用户明确修正或补充一条已有记忆，且需要保留同一记忆身份时调用。",
    avoid="不要覆盖用户未要求修改的内容；不要复用过期版本或旧来源。",
    prerequisites=(
        "memory_id/content_version 来自当前目录/读取结果；source_message_ids "
        "必须是已应用的用户消息；content 提供新正文。"
    ),
    effect="以版本校验保存记忆新版本；项目作品和当前对话内容不变。",
    returns="返回同一 memory_id 的新 content_version 和来源回执。",
    recovery="版本冲突时重读记忆并核对用户的新要求后再更新；不要盲目重放旧版本。",
    example='{"memory_id":"375104027219726700","content_version":"2","content":"偏好开场快速建立冲突，并减少重复解释。","source_message_ids":["375104027219726701"]}',
).render()
