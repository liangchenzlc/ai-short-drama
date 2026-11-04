from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="读取当前隔离工作区中的文本、代码或受支持媒体文件。",
    when_to_use="已通过 ls/glob 确认文件路径，或用户明确提供工作区内文件路径时调用。",
    avoid="不要读取宿主机绝对路径；不要把截断文本当作完整文件；不要将工具输出中的状态头复制进编辑内容。",
    prerequisites="file_path 是工作区文件绝对路径；offset 是从 0 开始的行偏移，limit 是读取行数。",
    effect="只读文件；大型文件可分页，媒体返回受控多模态内容。",
    returns="返回文件正文/媒体内容及可能的截断、分页提示。",
    recovery="路径错误时先用 ls/glob 找到正确文件；长文本沿返回分页参数继续读取。",
    example='{"file_path":"/workspace/notes.md","offset":0,"limit":100}',
).render()
