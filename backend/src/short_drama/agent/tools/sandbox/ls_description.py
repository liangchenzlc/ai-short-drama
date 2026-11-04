from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="列出当前隔离工作区指定目录中的文件和子目录。",
    when_to_use="探索工作区或确认目标路径存在后，再读取或编辑文件时调用。",
    avoid="不要用宿主机路径；不要将目录列表当成文件内容。",
    prerequisites=(
        "path 必须是沙箱工作区中的绝对路径；先使用此工具确认目标，再调用 read_file/edit_file。"
    ),
    effect="只读当前 scope 工作区，不读取宿主机文件。",
    returns="返回目录条目名称和类型。",
    recovery="路径不存在时用已知工作区根目录重新列出，不要扫描沙箱外路径。",
    example='{"path":"/workspace"}',
).render()
