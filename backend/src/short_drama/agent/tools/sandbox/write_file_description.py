from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="在当前隔离工作区创建文件或完整替换指定文件内容。",
    when_to_use="用户需要新建文件，或明确要求整份重写已有文件时调用。",
    avoid="不要用于小范围改动已有文件；已有文件先读取，局部替换使用 edit_file。",
    prerequisites="file_path 是当前工作区内的绝对路径；content 是要写入的完整文本。",
    effect="修改当前 scope 工作区文件，不直接保存到项目正文或共享存储。",
    returns="返回写入路径和操作结果。",
    recovery="写入失败时先读取目标文件状态；不要在路径不确定时换路径重写。",
    example='{"file_path":"/workspace/outline.md","content":"完整文本"}',
).render()
