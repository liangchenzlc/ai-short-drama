from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按真实方法名称和内容版本激活本任务所需的创作 SOP。",
    when_to_use="检索并判断某个方法适用后，需要将方法说明加入当前执行上下文时调用。",
    avoid="不要猜名称或版本；不要把加载 Skill 当作读取其所有附件，也不要重复加载同一激活。",
    prerequisites=(
        "name 和 content_version 来自 search_skills "
        "或用户明确选中的方法目录；purpose 说明本任务如何使用该方法。"
    ),
    effect="更新当前 scope 的 Skill activation 和上下文；不会修改作品或执行方法中的文件。",
    returns="返回 activation_id、冻结 manifest、方法正文来源引用及是否已激活。",
    recovery="加载失败或预算不足时先释放可释放的自动方法；后续读取附件须使用返回的 activation_id。",
    example='{"name":"drama-storyboard-planning","content_version":"2","purpose":"把已选剧情拆成连贯分场"}',
).render()
