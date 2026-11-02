"""Directory metadata for the upstream SubAgentMiddleware tool, composed at runtime."""

from deepagents.middleware.subagents import SubAgentMiddleware, TaskToolSchema

from short_drama.agent.tools.contracts import ToolSpec

DESCRIPTION = (
    "同步委派独立子任务给 workbench-task，由主助手整合摘要和产物。"
    "description 写清目标、必要背景、交付要求，不复制父历史或Skill正文。"
    "最多一层，默认三个并发，共享父Run预算。"
    "需要交接输入时，只在description末尾附一个 ```workbench-inputs 换行 JSON 换行 ``` 块。"
    "JSON仅允许 subjects（已有subject_key或kind:id列表）、resources（完整ResourceRef列表）、"
    "generation_ids（已有生成ID十进制字符串列表）、script（布尔，默认false）。"
    "每列表最多16项，块最多32768字符，description最多40000字符。"
    "这些声明只与父已获准范围求交，不能授权猜测身份；省略时子任务不能读取父既有对象、"
    "成果、剧本或文件。跨scope文件必须显式导出ResourceRef后交接并导入。"
    "成功返回的business_receipts及result_refs由真实子业务收据生成，父可按原operation_id"
    "读回成果、继续修改或再次明确交接；生成身份可查询和等待，摘要文本不授予权限。"
)

SPEC = ToolSpec(
    name="task",
    description=DESCRIPTION,
    args_schema=TaskToolSchema,
    handler=SubAgentMiddleware,
    effect="delegate",
    source="runtime",
)
