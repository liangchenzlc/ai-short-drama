from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按当前版本遗忘一条账号长期记忆，并使其来源不再作为有效上下文。",
    when_to_use="用户明确要求忘记某条记忆，或明确撤回该记忆来源时调用。",
    avoid="不要因内容暂时不相关就自行遗忘；不要把任意替代文本当成用户的遗忘指令。",
    prerequisites=(
        "memory_id/content_version 来自当前目录或读取结果；content 和 "
        "source_message_ids 须反映真实用户撤回/遗忘要求。"
    ),
    effect="写入遗忘版本并使对应记忆来源失效；不物理删除历史审计。",
    returns="返回记忆身份、更新版本及遗忘状态。",
    recovery="版本冲突时先重读并确认仍是用户要求遗忘的记忆，再用当前版本操作。",
    example='{"memory_id":"375104027219726700","content_version":"2","content":"用户要求不再保留该偏好。","source_message_ids":["375104027219726701"]}',
).render()
