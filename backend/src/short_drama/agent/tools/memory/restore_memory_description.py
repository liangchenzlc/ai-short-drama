from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="用新的明确用户来源恢复一条曾被遗忘的长期记忆。",
    when_to_use="用户再次明确提出该信息应作为未来偏好保留时调用。",
    avoid="不要仅因旧记忆仍在历史中就自行恢复；旧来源不能复活。",
    prerequisites=(
        "memory_id/content_version 来自当前记忆目录；source_message_ids "
        "必须指向本轮已应用的新用户确认；content 是当前确认内容。"
    ),
    effect="按当前版本恢复记忆并建立新来源关联，不改变作品正文。",
    returns="返回恢复后的记忆身份、新版本和新来源回执。",
    recovery="版本冲突时重读当前记忆；没有新的用户来源时不要调用恢复。",
    example='{"memory_id":"375104027219726700","content_version":"3","content":"用户再次确认希望保留开场快速进入冲突的偏好。","source_message_ids":["375104027219726702"]}',
).render()
