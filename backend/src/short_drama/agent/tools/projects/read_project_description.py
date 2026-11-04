from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="读取一个已获准项目的摘要和当前作品版本。",
    when_to_use="已从项目列表或当前任务上下文取得确切 project_id，且需要核对目标或版本时调用。",
    avoid="不要用它读取完整剧本、分镜正文或素材详情；这些内容由专用读取工具提供。",
    prerequisites="project_id 必须是用户明确指定或先前工具返回的项目 ID。",
    effect="只读，不修改项目、剧本或分镜。",
    returns="返回项目基本信息及当前剧本和分镜版本；写入前再核对对应写工具要求的版本。",
    recovery="项目不可访问时停止对该项目的操作，不要换猜测的 ID 重试。",
    example='{"project_id":"375104027219726336"}',
).render()
