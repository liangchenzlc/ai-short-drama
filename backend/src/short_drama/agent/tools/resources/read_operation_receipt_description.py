from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="核对当前范围的操作回执，按页回读被卸载的原始工具结果。",
    when_to_use=(
        "收到 tool_result_offloaded 时按 read_with 回读正文；委派任务返回 business_receipts 后，"
        "根据 operation_id 核对或交接真实成果。"
    ),
    avoid="不要从摘要文字推断操作身份；本工具不执行、不重试任何操作。",
    prerequisites=("operation_id 必须来自当前范围或明确交接的真实回执；不能凭摘要推断身份。"),
    effect="只读，返回信息受当前执行 scope 和交接关系限制。",
    returns=(
        "默认返回操作状态、业务回执、成果与撤销引用；include_result=true 时增加 result_page，"
        "含 content、total_characters、checksum_sha256、next_offset。正文每页默认4000最多6000字符。"
        "方法正文与资料仍使用对应方法工具，来源被遗忘、主体失效或方法退出后不能回读。"
    ),
    recovery=(
        "未找到回执表示该操作不在当前范围或未成功交接；回到父 task 的真实 business_receipts 核对。"
    ),
    example=(
        '{"operation_id":"375104027219726600","include_result":true,'
        '"character_offset":0,"character_limit":4000}'
    ),
).render()
