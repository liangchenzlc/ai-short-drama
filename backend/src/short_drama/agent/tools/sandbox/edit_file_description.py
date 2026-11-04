from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="在当前工作区文件中执行精确文本替换。",
    when_to_use="已经读取目标文件，并且需要局部、唯一且可核对的编辑时调用。",
    avoid="不要猜原文空格或缩进；不要将 read_file 的状态头包含在 old_string/new_string 中。",
    prerequisites=(
        "file_path 是工作区绝对路径；old_string "
        "必须是文件中的精确文本，默认要求唯一；replace_all 只在明确要全部替换时设 true。"
    ),
    effect="修改工作区目标文件；不会更改项目正文或其他 scope 的文件。",
    returns="返回替换结果及匹配数量/文件身份。",
    recovery="未匹配或非唯一时重新读取文件并提供精确片段；不要直接改为全局替换绕过歧义。",
    example='{"file_path":"/workspace/outline.md","old_string":"旧句","new_string":"新句"}',
).render()
