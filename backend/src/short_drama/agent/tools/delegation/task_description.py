from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="同步委派一个可独立完成的子任务，由主助手整合真实回执和结果。",
    when_to_use="任务可拆成独立、有明确验收产物的工作，且子任务不依赖父任务尚未作出的选择时调用。",
    avoid=(
        "不要复制父会话历史或 Skill "
        "正文；不要让子任务重复委派或越权读取未交接对象；摘要不授予权"
        "限。"
    ),
    prerequisites=(
        "description 写明目标、必要背景、输出要求和验收标准。需传入资源时，仅在结尾增加一个 "
        "workbench-inputs JSON 块，内容须来自已获准 "
        "subjects/resources/generation_ids；script 默认 false。"
    ),
    effect="创建同步子任务并等待完成，然后将结果交回主助手。",
    returns=(
        "返回子任务摘要、真实 business_receipts 和 result_refs；父按 "
        "operation_id 回读或再次明确交接成果。"
    ),
    recovery="成果未出现在真实回执时视为未交付，不能凭子任务摘要声称已保存；等待中的媒体沿原任务身份查询/等待。",
    example='{"subagent_type":"workbench-task","description":"检查已交接分镜的连续性；返回问题清单和引用来源，不修改作品。"}',
).render()
