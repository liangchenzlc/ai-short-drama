from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="分页查看当前账号已保存长期记忆的目录和版本摘要。",
    when_to_use="需要先找到某条记忆的真实 memory_id、标题或 content_version 时调用。",
    avoid="目录不含完整记忆正文；不要根据标题猜记忆 ID。",
    prerequisites="无需参数；offset/limit 按目录位置分页。",
    effect="只读，受账号记忆开关及当前账号范围限制。",
    returns="返回记忆摘要、真实 ID/版本和分页信息；正文由 read_memory 读取。",
    recovery="目录为空表示当前范围没有可用记忆；不要将作品或对话内容当作记忆目录数据。",
    example='{"offset":0,"limit":30}',
).render()
