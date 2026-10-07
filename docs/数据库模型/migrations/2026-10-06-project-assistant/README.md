# 项目级 AI 创作助手迁移

本次复用现有私人 Agent 九表和 Worker，不新增表。新 `agent_conversations.scope_version=2` 仅绑定本人和项目，分集、阶段、对象及任务字段均为空；原 V0/V1 记录和外键保持，不按标题推断、不合并历史。

新库使用[完整 SQL](../../schema.mysql8.sql)。已经完成 Agent 范围迁移的旧库使用 [001-project-assistant.sql](001-project-assistant.sql)，不能在尚无范围字段或缺少原 CHECK 的库盲目执行。该文件是单个 ALTER：将 `episode_id` 改为 nullable、替换范围 CHECK、增加项目对话列表索引。MySQL DDL 隐式提交，不会由应用自动执行。

## 执行与核验

1. 核对连接目标库、备份与 `SHOW CREATE TABLE agent_conversations`。记录总行数以及 V0/V1 数量；已有行应均有有效分集、本人及项目归属。停止相关 API/Worker 写入；按现有部署流程保留并恢复仍在运行的任务。
2. 仅在未完成本次结构变更时执行 SQL。已新增索引或已更换 CHECK 的部分执行现场需逐项核对后补缺，不重复整段执行、不删除业务行。外键类型和 unsigned 属性不变。
3. 使用 `inspect_agent_schema` 核验字段 nullable/default、索引列序、CHECK 表达式和 ENFORCED、外键及数据行数。V0/V1 必须仍有分集；V2 必须 `episode_id/stage/subject_type/subject_id/task_type` 全为空。原分集会话与候选数量不变。
4. 部署同版本 API、调度器和 Agent Worker，再恢复运行。旧代码不识别 V2；已有 V2 数据后不要将列改回 NOT NULL，应向前修复。

运行目的和每轮来源快照分别保存在既有 `agent_runs.checkpoint.purpose/context_snapshot` JSON 中，无新增列。私人消息、附件和 Skill 继续沿用现有作者与项目检查；新助手不创建候选或生成任务。接口见[项目助手 API](../../../api/assistant.md)。

本次本机业务库 `short_drama` 已实际执行 `001-project-assistant.sql` 的单个 ALTER：放宽 `episode_id` 为 nullable、替换 `ck_agent_conversations_scope`、新增 `idx_agent_conversations_project_chat`。执行前后旧会话均为 27 条，没有合并或删除；执行后 `inspect_agent_schema` 返回 `status=ready`、`gaps=[]`。本机运行结果保存在 `.runtime/project-assistant-ddl-result.json`，该运行产物不提交。此记录不代表其他部署环境已经升级。

其他环境仍应独立保存目标库、执行时间、SQL 版本、前后行数及结构验证结果。隔离测试使用随机 `_test` 数据库，不将应用库作为测试库；测试中的旧 NOT NULL 结构模拟需要临时去除并同名恢复分集外键，正式增量仍直接执行本目录单 ALTER。
