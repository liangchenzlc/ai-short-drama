# Harness 图检查点增量

2026-10-26，新增 `agent_graph_checkpoints` 与 `agent_graph_writes`，保存轻图状态、元数据及节点 pending writes。账号、Task、Run 外键与作用域守卫共同校验归属；唯一键隔离任务、namespace、checkpoint 及节点写入索引。

已有当前创作模式库先备份，停止 API、scheduler、Agent Worker 写入，在选定业务库执行 [001_graph_checkpoints.sql](001_graph_checkpoints.sql)，然后执行 `uv run python scripts/migrate_agent.py` 只读检查。应用不自动迁移。新空库直接使用 [完整 SQL](../../schema/schema.mysql8.sql)，不叠加此增量。

当前业务库 `short_drama` 已实际执行此 DDL，完整结构 118 表、Agent 27 表；完整 SQL 已按 ORM 重导出。运行时与部署见 [Harness 运行机制](../../../instruction/AI创作助手Harness运行机制.md)。迁移不改写既有任务、消息、作品或 Skill 版本。

回退应用前须停止新 Harness 任务，保留原 Run／Turn／Operation 及这两表用于审计和恢复；不要删除仍有任务依赖的检查点。旧任务冻结原引擎；不能把在途 Harness Run 改成旧引擎继续执行。
