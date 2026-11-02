# 前端 API 约定

`api/assistant/conversations/requests.ts` 提供 `settings/updateSettings`，对应 `GET/PATCH /assistant/settings`；`types/assistant/api.ts` 的 `AssistantSettings/AssistantSettingsPatch/AssistantRuntimeLimits` 保持完整设置与独立十进制 `settings_version`。409 保留设置草稿并重读，422 显示部署上限。Skill `ResourceKind` 包含 `script`，文件读取和导出不执行脚本。

HTTP 公共契约见[接口说明](../../../docs/api/README.md)，目录树见[目录与职责维护](../../../docs/instruction/目录与职责维护.md)。

## 请求与类型

- http.ts 是唯一 Axios 客户端，统一 API 基址、Cookie／CSRF、超时与错误映射；不自动重试写入、不全局弹 toast。
- api 按业务／功能组织请求函数，具名 DTO 放 src/types 对应目录。字段保留 snake_case，ID 和版本保持十进制字符串。
- API 不依赖 features、pages、app；表单转换、状态生命周期与错误文案由业务功能承担。通用组件只接收数据及回调。
- 宿主内部使用 @host/；画布只通过 integrations/canvas 的公开桥接访问宿主。

`api/assistant/settings/mcp-tokens.ts` 提供 `GET/POST /account/mcp-tokens`、`GET /account/mcp-tokens/options`、`DELETE /account/mcp-tokens/{id}`，沿统一 Cookie／CSRF 请求层。DTO 在 `types/assistant/settings/editor.ts`：`McpAccessToken` 只有公开字段，`McpTokenCreated` 额外包含一次性 secret；没有服务端 status 字段，界面只根据 expires_at 派生到期状态。完整 secret 仅保留在本次创建弹层，关闭即清，不写 localStorage、草稿或日志。选项取服务端项目／工具目录，见 [MCP API](../../../docs/api/mcp.md)。

## 保存与恢复

正文、分镜、素材、声音和成片使用各自编辑会话串行保存。409 保留本机输入；旧响应不能覆盖新输入；请求结果未知时先确认原回执，不能把读到旧基准当成保存成功。会话按账号与稳定资源隔离，页面卸载只解除订阅。恢复稿保留服务端基准、最后输入与未知请求；存在冲突或损坏记录时保留原文供核对和下载。

script 文档与故事设定分别由 api/creation/writing/documents 和 features/creation/writing 管理；正文共享项目 content_version，四项故事设定有独立 row_version。小说／大纲读写与改编草稿用途已删除。选区改写先 flush，冻结 Unicode 码点选区、载荷与幂等键，成功后精确替换并保留撤销边界，见[写作契约](../../../docs/api/writing-workspace.md)。

素材图、分镜图、分镜视频的私人生成草稿在 api/creation/drafts，下一次生成参数不修改作品描述或已采用媒体。生成前等待草稿保存并核对草稿版本、当前来源版本及对象归属。任务／候选采用继续执行版本与来源核对。

## 助手与 Skill

api/assistant/conversations 提供当前项目的会话、任务与事件。global 只是现行路径前缀，不代表跨项目聊天。附件走 /assistant/conversations/{id}/attachments；模型能力在 api/ai-config/assistant，方法包在 api/skills/catalog，类型分别放对应 types 目录。

`tracePage/traceResource` 分别读取 `/assistant/conversations/{id}/runs/{run_id}/trace` 与其块内受控详情，分页默认50，调用方不传存储定位。唯一块 DTO 位于 `types/assistant/trace.ts`，唯一投影为 `features/assistant/runtime/assistant-trace.ts`；任务快照含 trace_blocks／has_more_trace／trace_before_seq，按 block_version 的 BigInt 和各通道 UTF-8 offset 合并。主会话从同一状态渲染简洁工具／Skill／沙箱行和子轨迹，仅展示操作、对象与状态，真实 reasoning 可展开；通过“载入更早”分页，受控资源接口保留且聊天不提供工具详情入口，见[统一执行轨迹](../../../docs/api/assistant.md#统一执行轨迹)。

`answerQuestion(operationId, body, key)` 调用 `/assistant/operations/{id}/answer`，沿统一 HTTP 层携带 Cookie／CSRF 和固定幂等键；body 为 question_version、outcome、values、additional_text，不接受 interrupt 身份。相同答案重试返回原 answer_message_id，改答案或过期问题返回409，字段无效422。`AssistantTaskSnapshot.pending_questions` 提供 scope、可答状态与已答／已取消状态，仅可答时 request 包含题目，否则为 null。题目仅支持 single_select／multi_select，每题 2–4 个带 recommended 的候选，单选恰好推荐一项，多选至少推荐一项；“其他”以自定义字符串写入 values，多选最多增加一个。Dock 在聊天滚动区外展示多题与问题组切换，提交成功后隐藏；新界面 additional_text 为空。waiting_input 是同一活动 Run，答案消息 kind=tool_answer 且正文为自然语言，不得当普通输入再发送。只有明确跳过才提交 skipped／空 values，allow_skip=false 时禁止跳过。

快照轨迹页属于全会话，后续 tracePage 按 Run 分页；每个 Run 独立保存 next_before_seq／has_more，旧 Run 经既有 taskState 的 before_task_id 分页发现。普通刷新保留已读进度；resync 保留块数据，从最新快照重建 Run 页和 Task 发现游标，旧页迟到响应不推进新游标。Task 页与 Run 历史全部耗尽后才结束“载入更早”。

问题界面从 taskState 按 operation_id 去重。未知答案先重读 taskState；没有该项时再读取原 OperationDetail 区分已答、跳过、停止和未知，不能把 pending 缺失推定为成功。媒体数量仅在新建 Task 且草稿显式覆盖时附带 media_limits，undefined 省略以继承默认，null 不限，0 禁止；已有 Task 展示实际冻结值，活动补充和停止续聊均省略该字段，未知重试保持原 body／幂等键。运行设置复用现有 settings/updateSettings，读取新版本不覆盖未保存编辑，旧保存响应也不覆盖之后输入。

发送冻结全文、附件、Skill 内容版本、目标、音轨选择及幂等键；未知回执与普通草稿分开持久化。原请求核对不受当前 pending 附件或页面选择影响；明确未受理才能修正后新建请求。当前唯一引擎为 assistant-runtime.1，控制 DTO 仅允许 stop；resume/redirect 等旧字段或控制返回422。活动任务补充使用普通消息，停止后普通消息建立新 Run 并保留已结算成果；成功任务的新 stop 返回409，已受理同键请求仍可回放。006终结的旧编排只展示历史，不再按旧引擎恢复。业务事件刷新编辑区域时继续保留脏稿。

Skill 创建受理不明用原键只读查询回执；文件按准确内容版本读取，切换取消旧请求，迟到内容不能覆盖当前文件。具体契约见[Skill API](../../../docs/api/skills.md)和[助手 API](../../../docs/api/assistant.md)。

## 成片、媒体与环境

导出、预览、恢复及重试固定原请求和原键，受理未知不能换参自动重提。同步失败保留编辑和上次状态；媒体只持久化稳定 ID，签名 URL 过期后重读。画布内部契约继续由独立子包负责，宿主集成桥提供会话、DTO、助手与共享图片工具。

.env.local 仅放公开前端设置；VITE_API_BASE_URL 会进入浏览器，不能放凭据。开发代理使用 API_PROXY_TARGET；生产部署先代理 API 再做 SPA fallback，见[运行指南](../../../docs/instruction/开发运行与验证.md#部署与维护)。
