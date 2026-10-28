# Agent 加固增量（2026-10-27）

本轮把 Harness 的交付要求从"模型自述"改为"服务端预置 + 落表 + 发布前强制"，并为停滞监督增加可去重的证据摘要。DDL 只有一份：[010-task-requirements.sql](010-task-requirements.sql)。

## 变更内容

| 对象 | 变更 | 用途 |
| --- | --- | --- |
| `agent_task_requirements` | 新表 | 服务端 draft、确认状态、真实原文偏移、每项尝试次数 |
| `agent_operations.result_digest` | 新列（可空） | 判断"同一结果是否已经出现过"，供滑窗停滞判定去重 |
| `agent_operations.evidence_kind` | 新列（可空） | `write/read/observation/skill/none`，供窗口统计与审计 |
| `idx_agent_operations_evidence` | 新索引 | `(task_id, result_digest)` |

旧行两列保持 `NULL`：NULL 不计入去重，也不参与停滞统计，等价于"未知"，不需要回填历史正文。

## 回填语义

`agent_tasks.effective_state.task_contract.requirements` 中的旧要求按 `JSON_TABLE` 搬入新表：

- `origin='agent'`、`state='confirmed'`，沿用原 `key/kind/minimum_count/artifact_kind/asset_kind`；
- 旧 JSON 没有原文偏移，因此 `quote_start = quote_end = 0`（结构合法但表示"无精确偏移"），描述追加 `[legacy]`；
- 只回填 `source_message_id` 能对上 `agent_messages` 的行，避免伪造外键；
- 同一任务已有行时跳过，可安全重入。

带精确偏移的任务仍走新路径；只含 `[legacy]` 行的任务继续按原 JSON 契约验收，不会被当成新格式。

## 执行顺序

前置：备份 MySQL 与 MinIO，保留原 `ENCRYPTION_KEY`，显式设置 `AGENT_ENABLED=false` 并停止 API、scheduler 与所有 Agent Worker 写入。

```powershell
# 在 backend/ 下执行；先只读检查结构缺口。
uv run python scripts/agent_migration.py --precheck
# 执行 010 DDL（新库直接使用 docs/database/schema/schema.mysql8.sql，不叠加本增量）。
# 逐条执行 010-task-requirements.sql 后，再补齐 ORM 声明的新表：
uv run python scripts/agent_migration.py --apply
uv run python scripts/agent_migration.py --precheck
```

`--apply` 按 ORM 补缺表，不会改列；`agent_operations` 的两列与新索引必须由 010 的 `ALTER` 完成。010 **只执行一次**（`ALTER` 不可重入，重复执行会因列已存在而失败）；回填段落本身可重入，失败后可先核对已回填行再重试该段。完成后恢复 `AGENT_ENABLED` 并重启 API、scheduler、Agent Worker 与前端构建产物。

## 执行证据

**本增量尚未在任何数据库执行。** 交付仓库时只运行了静态检查与单元测试；`scripts/export_schema.py` 已按 ORM 重导完整 SQL（119 表）。执行后需把本机 `short_drama` 库的表数（预计 119 表 / 28 张 Agent 表）、`--precheck` 的 `gaps=[]` 与会话结果补到本节。

## 回滚

- 应用侧优先：把新任务的 `agent_policy` 写回旧值、`AGENT_STOP_AT_BOUNDARY=false`、`AGENT_REVIEWER_MODE=same_model`，即可回到旧行为，无需动表。
- 不删除新表/新列：已冻结任务、回执与审计仍引用它们；删除会破坏恢复。
- 已升级的 API 与旧 Worker 不能混跑：新列与新工具名要求 API、scheduler、Agent Worker、前端同版。
