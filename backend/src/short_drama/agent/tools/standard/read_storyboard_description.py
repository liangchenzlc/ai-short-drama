from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="分页读取获准标准项目的分镜、关联素材和镜头版本。",
    when_to_use=(
        "修改、重排、关联或删除已有镜头前，需要核对 shot ID、row_version 和 "
        "storyboard_version 时调用。"
    ),
    avoid="不要将 project_scripts 中的整篇剧本 ID 当作 shot ID；本工具读取的是分镜记录。",
    prerequisites="project_id 来自当前任务目标；offset/limit 用于镜头分页。",
    effect="只读，不修改镜头或素材关联。",
    returns=(
        "返回镜头字段、row_version、集合 storyboard_version 和分页信息；后续写入使用这些真实版本。"
    ),
    recovery="返回空列表时不要构造已有镜头 ID；若需要新建，可使用写入工具的 create_shot 分支。",
    example='{"project_id":"375104027219726336","offset":0,"limit":20}',
).render()
