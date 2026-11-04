from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="等待先前已受理的媒体任务进入可返回结果的状态。",
    when_to_use="下一步依赖生成结果，且已经持有受理回执中的 task_id 时调用。",
    avoid="不要传 generation_id 代替 task_id；不要用等待工具代替提交或重试生成。",
    prerequisites="task_ids 必须来自原生媒体受理回执；重复 ID 无效。已完成任务可直接返回。",
    effect="可能挂起当前 Run 等待异步事件；不会提交任务、重新计费或改变媒体输入。",
    returns="返回每个任务的最终/当前状态及结果引用；任务成功状态与真实产物引用共同证明可用。",
    recovery="等待被中断后沿原 task_id 再次等待或查询状态；不要创建新提交来恢复原等待。",
    example='{"task_ids":["375104027219726500"]}',
).render()
