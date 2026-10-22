# 项目 AI 创作助手会话约束增量

适用于已完成创作模式切换与 [Agent 001–009](../2026-10-21-agent-system/README.md) 的现有数据库。空库直接使用[完整 SQL](../../schema/schema.mysql8.sql)，无需重复执行本增量。应用不会自动迁移。

## 变更

[001-project-scope.sql](001-project-scope.sql) 使用一条 `ALTER TABLE` 原子替换 `agent_conversations.ck_agent_conversations_scope`：V3 允许绑定 `project_id`，其余 stage、subject 和 task_type 字段仍须为空；V0/V1/V2 约束保持原规则。没有新表、新列、数据删除或历史归属回填。

新前端只创建、恢复当前项目会话；历史未绑定项目的 V3 保留 NULL，通过旧兼容契约访问，不猜测其所属项目。会话所属项目不可通过改名变更，服务端另校验本人项目权限、模式及画布归属。

## 执行与核验

先核对目标数据库、当前 CHECK 和在途助手任务，备份原约束及会话计数；在数据库运维客户端执行上述 SQL，再部署同版 API 与 Worker。核对新 CHECK、会话行数及 Agent schema readiness。SQL 的生成检查不会实际执行数据库升级。

2026-10-22 本机实际执行结果：

| 项目 | 结果 |
| --- | --- |
| 目标 | `short_drama`，MySQL 8.4.11 |
| 变更前在途助手任务 | 0 |
| `agent_conversations` 行数 | 变更前 1，变更后 1 |
| 当前总表数／Agent 表数 | 117／25，未新增表 |
| `inspect_agent_schema` | `status=ready`，`gaps=[]` |

项目创建、列表隔离、跨账号拒绝、默认目标、请求重放、画布目标固定及项目归档后的执行拦截由独立 MySQL 回归覆盖。其他部署需自行核验，不以本机结果代替。

## 回退边界

旧 CHECK 要求全部 V3 的 `project_id` 为空。回退前必须查询是否已存在绑定项目的 V3；存在时不能直接恢复旧约束，也不能自动清空其归属。应保留当前结构，使用同版代码处理，或通过已核验备份按运维方案恢复。单独回退代码不能恢复旧会话语义。
