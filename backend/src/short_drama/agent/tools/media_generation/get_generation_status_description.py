from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="查询本人在获准项目内已存在的媒体生成任务状态及产物引用。",
    when_to_use="已通过提交工具取得真实 generation_id，且需要核对任务进展或完成结果时调用。",
    avoid="不要传入猜测的任务 ID；不要用此工具创建、重试或重新提交任务。",
    prerequisites="project_id 和 generation_ids 来自原受理回执或之前的状态查询。",
    effect="只读，不会再次调用供应商或产生新生成费用。",
    returns=(
        "逐项返回任务状态、错误、可用操作标记及媒体/产物引用；只有 "
        "succeeded 和真实产物引用表示生成归档完成。"
    ),
    recovery=(
        "状态仍为 queued/running 时继续查询或调用 wait_generation；受理未知时仍使用原 "
        "generation_id 查询。"
    ),
    example='{"project_id":"375104027219726336","generation_ids":["375104027219726500"]}',
).render()
