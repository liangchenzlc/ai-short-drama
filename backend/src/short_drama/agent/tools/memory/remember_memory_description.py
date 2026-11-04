from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="保存用户明确表达、适合跨会话复用的账号级长期偏好或稳定事实。",
    when_to_use="用户明确提出希望长期记住，或明确说明该偏好将持续适用时调用。",
    avoid="不要保存助手推测、临时项目细节、敏感凭据或只在本轮成立的信息。",
    prerequisites=(
        "source_message_ids 必须指向当前任务已应用的真实用户消息；content 仅记录该来源支持的事实。"
    ),
    effect="新增账号长期记忆并写入来源关联，不修改项目正文或会话历史。",
    returns="返回新记忆的真实 memory_id、content_version 和保存状态。",
    recovery="来源校验失败时重新核对真实用户消息，不要用助手回复 ID 替代。",
    example='{"kind":"preference","memory_key":"narrative_pacing","title":"叙事节奏偏好","content":"偏好开场快速建立冲突。","source_message_ids":["375104027219726700"]}',
).render()
