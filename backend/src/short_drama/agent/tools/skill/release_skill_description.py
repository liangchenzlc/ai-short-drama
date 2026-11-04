from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="释放当前任务中可释放的自动 Skill 正文以回收上下文预算。",
    when_to_use="某阶段已完成，且自动加载的方法正文不再影响后续工作时调用。",
    avoid="不要试图释放用户手选方法；释放不删除历史回执或撤销已发生操作。",
    prerequisites="activation_id 来自当前 scope 的 Skill 激活记录；只释放 source=auto 的激活。",
    effect="收回当前上下文中的方法正文，不改变项目、Skill 版本或历史任务成果。",
    returns="返回释放状态及相应 activation 身份。",
    recovery="手选方法由用户管理；不能释放时继续沿现有 activation 使用或通过后续摘要处理。",
    example='{"activation_id":"375104027219726700"}',
).render()
