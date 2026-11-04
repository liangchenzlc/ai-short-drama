from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="在缺少会改变创作方向的关键信息时暂停并向用户收集结构化选择。",
    when_to_use="不同答案会导致明显不同的剧情、目标、角色或写入方案，且无法从已授权上下文判断时调用。",
    avoid="不要用它发送普通进度说明、已知事实确认或把开放式写作任务拆成无必要的问题。",
    prerequisites=(
        "title 概括问题组，question 说明需要决策的事项；每个 field 提供 2–4 个具体 "
        "options 和推荐项。界面自动提供“其他”，不要把“其他”伪装成选项。"
    ),
    effect="创建持久问题并挂起当前 Run，直到用户回答、跳过或任务被停止。",
    returns="恢复后返回回答状态、结构化 values、补充文本及关联消息身份；跳过时返回 skipped 状态。",
    recovery="等待期间不要重复调用创建第二组问题；答案过期或任务已停止时遵循返回状态并重新核对当前目标。",
    example='{"title":"确定反转方向","question":"主角如何发现真相？","fields":[{"key":"reveal","label":"发现方式","type":"single_select","required":true,"options":[{"value":"recording","label":"录音证据","recommended":true},{"value":"witness","label":"证人出现","recommended":false}]}],"allow_skip":true}',
).render()
