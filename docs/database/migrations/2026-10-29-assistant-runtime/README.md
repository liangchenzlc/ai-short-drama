# 创作助手执行图与范围迁移

003 在既有 `agent_preferences` 增加 custom_instructions、settings_version、model_call_limit、output_token_limit、subagent_max_concurrency、default_media_limits，保留 policy_version 与实时媒体开关；新预算范围和并发上限由 CHECK 约束。旧偏好保留，未保存助手设置的 settings_version=0，媒体默认数量为空对象。

004 为 `agent_operations` 增加 nullable JSON wait_state 及 kind CHECK，Run／Task／ToolCall／Operation 增加 waiting_input，Run.active_conversation_id 生成列将 waiting_input 计为活动；`agent_messages.kind` CHECK 允许真实用户 tool_answer。问题和答案复用 Operation.arguments／wait_state，不增加问题表。Operation取消仍使用 failed／agent_cancelled，展示取消状态从该事实生成。

005新增 `mcp_access_tokens`，保存本人具名令牌摘要、提示、固定非空作品／工具范围、可选有效期／IP及撤销时间；hash唯一，owner外键和scope CHECK均强制。AuditEvent增加nullable source／token_id，旧审计保持NULL，新增token外键和来源CHECK；撤销保留令牌行及审计关联。

依次执行 `001-graph-and-scopes.sql`（119→120表）、`002-business-invocations.sql`（120→121表）、`003-assistant-settings.sql` 与 `004-durable-waits-and-questions.sql`（均保持121表）、`005-mcp-access-tokens.sql`（121→122表），再由下述离线脚本执行 `006-retire-harness.sql`（122→121表）。ORM是结构唯一来源，全量SQL由 `backend/scripts/export_schema.py` 生成；已验收001–005不重写。

部署须先停止 API 与助手 Worker，备份并核对起点为 119 表的当前结构，再执行此增量；MySQL DDL 会隐式提交，不能当作可整体回滚。应用不会自动执行迁移。本阶段只在专用 MySQL 随机 `_test` 库预演，未执行应用库。

旧 delivery namespace / JSON checkpoint 不能恢复到 typed saver，迁移只清两张图表；不双读。保留全部会话、Task、Run、消息、历史 Turn/ToolCall、Operation、已有媒体任务与成果；包括 `task_id IS NULL` 的历史 Run，均回填一个 `root` 范围，不恢复旧编排。后续切换步骤另行终结旧在途编排。

`agent_execution_scopes` 用 `(run_id, scope_key)` 唯一：根为 `root`，子范围为 `tool:<父委派 ToolCall 数据库主键>`。父调用外键在 ToolCall 增补唯一键后建立，所有 Turn/ToolCall/Operation 以 `(scope_id, run_id)` 约束归属。同 scope 可有多个在途 Operation，删除原 `active_task_id` 唯一锁；仍保留单工具调用幂等、Run 租约和业务资源版本锁。沙箱绑定只存服务、容器、卷、镜像及生命周期，不包含凭据或 SDK。

图表保留完整 MEDIUMTEXT namespace，并以 SHA-256 BINARY(32) 参与唯一索引；查询同时比较完整 namespace。typed serializer 使用 `JsonPlusSerializer(pickle_fallback=False)` 与 codec/LONGBLOB，metadata 保持校验后的 JSON。删除会话的 purge 清理所有 namespace 的 checkpoint 与 pending writes。

002 新建 `business_tool_invocations`，以账号、工具及稳定 request_id 唯一，记录参数摘要、source／token_id、状态、业务结果与引用。`agent_operations.business_invocation_id` 的列与外键同在002增加，001不提前引用该表。来源、账号、工具与请求身份受写入守卫保护；对话 purge 清空关联回执的私人结果及变更快照，保留业务成果和幂等身份。

已在专用 MySQL 随机 `_test` 库从119表基线顺序预演001→002→003→004，逐项核对 ORM 的全部 Agent 约束、索引、类型与 readiness。8条原账号、项目、模型、会话、Task、消息、Run和原生生成任务记录完整保留，root scope回填、waiting_input生成列、tool_answer及wait_state合法／非法kind均已验证。未修改应用库，未禁用外键或提升应用账号权限。

执行后在 `backend/` 重新导出 schema，运行真实 MySQL saver/scope 用例及结构 readiness 核对。不得对应用账号增加测试建库权限。

2026-10-30另在专用MySQL随机 `_test` 库从冻结119表基线顺序预演001→005。Agent／MCP全部列、索引、CHECK、FK和类型与ORM一致，identity readiness通过；保留原8条账号／项目／模型／会话／Task／消息／Run／原生任务及1条历史审计，新增审计字段仍为NULL。实际验证非法source与无效token外键被拒绝，未禁用外键、未修改应用库。

## 006：离线切换与退役

当前版本新 Run 的引擎标记为 `assistant-runtime.1`，与旧 `langgraph-harness.1` 明确区分。旧 Run 按 `checkpoint.engine` 筛选，缺标记也视为旧运行；旧引擎已有 typed 图仍不能续跑，006仅清这些 Run 的 checkpoint/writes，当前引擎的图保留。旧活动 Run 取消并撤销投递、租约，旧 Task 结束并提示“执行引擎已更新，请重新发送继续”。历史成功状态、真实用户 Message/Input、业务成果、AsyncTask 身份和旧媒体回调所需的 `checkpoint.authorization.steps` 保留。

先停止 API 和 Agent Worker，并备份数据库，再执行001–005。保持 `AGENT_ENABLED=false`，在 `backend/` 执行 `uv run python scripts/agent_runtime_migration.py --precheck`；确认输出无 blocker 后执行同脚本 `--apply`。应用不会自动迁移。脚本不创建/删除账号、作品或业务库，不能用全量 schema 覆盖旧库。切换完成后同版发布 API/Worker/前端，再启用新执行。

预检查只读：核对助手全部表的列/类型/默认值/生成列、主键、索引、外键、启用的 CHECK 和表选项，且只接受冻结119表经过001–005及006逐条DDL的已审结构。未知结构、有效 Run/MemoryJob 租约或发布租约、`agent_enabled=true`、Agent 队列仍有消费者或无法确认消费者时均拒绝 apply。消费者以 AMQP passive queue declaration 的 `consumer_count` 核对；本项目禁用了 Celery remote control，`inspect/ping` 无响应不能作为 Worker 已停证据。离线期间须保持入口和 Worker 停止，脚本不替代进程管理。

执行 DDL 前在一个事务中先归档：ToolCall 的真实 `review_*` 人工审核字段写入 `result.historical_runtime_audit.human_review`；Task 的 `media_evidence/resource_outcomes` 写入其实际 Operation/ToolCall 的历史审计；Operation 的 `result_digest/evidence_kind` 也保留为审计。它们不形成新授权或新像素交付身份。`AgentArtifact.apply_receipt/applied_by/applied_at` 及原生候选采用流程保留。仍在执行的已受理媒体及 `generation_task_id` 不取消，旧 collector 仅归档并保留上述审计，不复活旧 Run；未受理的旧操作结束。`task_state` 维护作业与 `agent_task_requirements` 退役，长期记忆 summary/extract/purge 保留。

同一离线事务规范化旧 Run 的 ToolCall／Operation 中仍有消费者的四个名称：`submit_generation_batch`、`apply_standard_patch`、`apply_canvas_patch`、`set_image_group_primary` 增加 `workbench__` 前缀，各行将原名保存到 `result.historical_runtime_audit.tool_name`。不改参数哈希、请求幂等身份或无关旧工具，不注册运行时别名；重复 apply 不覆盖原名审计。媒体 collector 的等待和终态回写继续保留该审计，已完成旧操作沿当前 undo Service 消费。

“继续归档”要求原生请求确已外发：保留 `sent`／`provider_task_id` 后的 poll／save，切换后不得把仍为 `prepared`／`submit` 的旧请求当作已付费请求重发。真实旧结构回归采用测试目录内固定 119 表 DDL，SHA256 与来源说明见 [迁移夹具](../../../../backend/tests/fixtures/agent_runtime_legacy/README.md)；只导入冻结在途收据，以本地媒体适配器延迟 poll，再执行正式归档、collector、预占结算和 undo，不构成真实供应商验收。

MySQL DDL 隐式提交。每条006 DDL后核对结构摘要再继续；中断后只允许在已知阶段重跑同一脚本，数据归档与旧编排结束均幂等。未知阶段应停止并比对备份，不手工跳过门禁。回退须停新执行、保存切换后的新业务成果，再恢复迁移前数据库与对应旧代码，不能让旧 Worker 读取新图。

2026-10-30专用13307随机 `_test` 库已从冻结119表顺序演练001→006，最终121表且 Agent/MCP readiness通过。实际核对消息/账号/项目/原生任务/历史审计、人工审核和媒体观察事实、已受理生成身份以及 callback authorization，重复 apply 无变化；当前引擎真实 typed 图保留和旧图清理另由 MySQL 集成测试覆盖。该演练的 Worker 消费者使用明确受控探针，不构成真实 RabbitMQ 停 Worker 验收；未修改应用库。
