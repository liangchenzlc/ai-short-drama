from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="回读当前任务范围内先前成功操作实际保存的正文成果。",
    when_to_use="写入工具已成功后，需要核对保存内容或继续处理较长结果时调用。",
    avoid="不要把模型回复、失败操作或未交付子任务摘要当作已保存成果。",
    prerequisites=(
        "operation_id 必须取自 describe_creation_target 的 result_sources "
        "或本任务真实成功回执；ids 只能是该来源公开的结果身份。"
    ),
    effect="只读；不能重放原 operation，也不能扩大任务范围。",
    returns="返回所选白名单成果及有限长度文本片段；长内容给出继续读取游标。",
    recovery="operation_id 不在来源清单时重新读取成果目录并采用其中 ID；不要手工拼接 ID。",
    example='{"operation_id":"375104027219726600","ids":[],"character_offset":0,"character_limit":4000}',
).render()
