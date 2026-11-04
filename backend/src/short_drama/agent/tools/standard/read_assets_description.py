from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="分页读取获准标准项目的素材及其当前版本。",
    when_to_use="写入或关联角色、场景、道具前，需要确认真实素材 ID 和 row_version 时调用。",
    avoid="不要用名称代替素材 ID，也不要把图组媒体 ID 当作素材 ID。",
    prerequisites="project_id 来自当前任务目标；offset/limit 用于素材列表分页。",
    effect="只读，不创建、修改或关联素材。",
    returns="返回素材身份、字段、row_version 和分页信息；写入时沿用读取到的 ID 与版本。",
    recovery="没有结果时确认项目模式和授权范围；分页继续使用服务端返回的 offset。",
    example='{"project_id":"375104027219726336","offset":0,"limit":20}',
).render()
