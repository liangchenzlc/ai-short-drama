from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按获准项目或画布一次性受理一个或多个图片、视频或音频生成请求。",
    when_to_use="用户明确要求生成媒体，且已核对本人实时生成策略、目标版本、模型配置和必要来源时调用。",
    avoid="不要把受理当作生成完成；不要因响应未知而换请求身份再次提交；不要把普通文本创作交给此工具。",
    prerequisites=(
        "project_id 必须是获准项目；每个 request 的 target_kind "
        "决定其专属字段。既有目标版本及模型版本须来自最近读取结果；引用媒体使用真实 ID。"
    ),
    effect="可能产生费用。成功只表示批次已受理并获得真实异步任务身份，不表示媒体已完成或已归档。",
    returns=(
        "返回每个请求的 generation_id、受理状态和原生任务引用；后续用 get_generation_status 查询，"
        "用 wait_generation 等待。"
    ),
    recovery="受理未知时按原请求/幂等身份查询回执，不生成新身份重发；失败项按返回的安全错误处理。",
    example='{"project_id":"375104027219726336","requests":[{"target_kind":"asset","target_id":"375104027219726400","row_version":"1","model_config_id":"201","model_version":"1","prompt":"雨夜车站的角色立绘"}]}',
).render()
