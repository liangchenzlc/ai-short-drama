from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="读取当前账号助手的媒体生成开关及各类型是否可执行。",
    when_to_use="准备生成媒体前，需要判断本次账号策略是否允许提交时调用。",
    avoid="不要把此结果当成项目权限、模型可用性或供应商成功保证；此工具不能修改设置。",
    prerequisites="无需输入参数；结果按调用时账号配置计算。",
    effect="只读，不改变开关、权限、模型配置或任务状态。",
    returns="返回媒体总开关和各类型能力状态；提交时仍须遵循提交工具及服务端实时校验。",
    recovery="能力关闭时停止媒体提交并说明限制，不要反复调用以尝试打开开关。",
).render()
