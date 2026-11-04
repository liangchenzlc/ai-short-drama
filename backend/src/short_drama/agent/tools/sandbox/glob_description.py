from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按 glob 模式查找当前隔离工作区中的文件路径。",
    when_to_use="知道文件类型或目录模式但不确定精确路径时调用。",
    avoid="不要将 glob 模式用于内容搜索；内容搜索使用 grep。",
    prerequisites="pattern 支持常见 *、**、?、字符组和花括号模式；path 可选且相对工作区。",
    effect="只读路径目录，不读取文件内容。",
    returns="返回匹配的工作区文件路径。",
    recovery="无结果时缩小/修正 pattern 或先用 ls 查看目录，不要遍历宿主机。",
    example='{"pattern":"src/**/*.py","path":"/workspace"}',
).render()
