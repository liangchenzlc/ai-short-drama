from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="在当前工作区文件内容中搜索逐字文本。",
    when_to_use="需要找到包含特定字面文本的文件或行时调用。",
    avoid="pattern 是字面字符串而非正则；查文件名用 glob，复杂正则使用受限 execute 中的 rg。",
    prerequisites=(
        "pattern 为要查找的原文；path/glob 可选地限制搜索范围；output_mode 选择文件、内容或计数。"
    ),
    effect="只读工作区文件，不执行匹配文件。",
    returns="按 output_mode 返回匹配路径、行内容或计数；max_count 可限制总匹配数。",
    recovery=(
        "结果截断时缩小 path/glob 或提高合理的 "
        "max_count；不要因没有匹配就断言文本不存在于所有资源"
        "。"
    ),
    example='{"pattern":"storyboard","path":"/workspace","glob":"*.py","output_mode":"content"}',
).render()
