from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="为当前 ProjectScript 的明确选区保存一份待审剧本修改建议。",
    when_to_use="用户要求修改已保存原稿，并已读取同一 script_id/content_version 与选区时调用。",
    avoid="不要用于直接改分场、改画布或声称正文已更新；提案须由用户在前端审阅并明确采用。",
    prerequisites=(
        "script_id、content_version、start/end "
        "必须来自最近一次成功的原稿读取；start/end 是半开字符区间。"
    ),
    effect="保存私有候选，不改变当前剧本正文或 content_version。",
    returns="返回提案 operation 身份、原文区间、替换正文及采用所需版本。",
    recovery="若来源版本变化，重新读取当前原稿后再生成提案；候选创建成功不等于已采用。",
    example='{"script_id":"375104027219726400","content_version":"3","start":0,"end":12,"replacement":"新的剧本片段"}',
).render()
