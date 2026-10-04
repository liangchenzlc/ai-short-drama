# Agent 会话与运行迁移

当前升级程序在已完成账户/协作迁移的数据库创建九张 Agent 表，不更改既有字段或数据，不回填历史对话。完整新库 SQL 已包含这些表；新库不要另跑本迁移。已经完成早期七表升级的旧库可执行 [Agent 附件与 Skill 增量](../20261003_agent_context.sql) 补齐两表，并运行当前预检。

| 表 | 读取范围 |
| --- | --- |
| agent_conversations / agent_messages / agent_runs / agent_turns / agent_tool_calls / agent_events | 会话所有者，且仍有项目访问权限 |
| agent_artifacts | 候选创建人，且仍有项目访问权限；采用后通过业务作品接口共享结果 |
| agent_attachments | 会话所有者；二进制上传保持个人媒体范围，历史消息引用保留 |
| agent_skills | 技能所有者；Markdown 内容版本在发送时冻结 |

会话限定一个分集，所有父链一致性仍由 Service 验证。活动 Run 通过生成列唯一索引限制每个会话最多一个；终态释放占位。消息与事件序号分别属于会话，通过同一会话行锁分配。工具审批、幂等意图和原生生成任务关联由运行层持久化；本人候选引用原生生成结果，保留类型化建议和不可变来源快照。

Agent 默认开启；已有库迁移前显式设置 `AGENT_ENABLED=false`，确认目标库及备份，暂停相关 API/调度器/Worker 写入。在 `backend/` 执行：

```powershell
uv run python scripts/agent_migration.py --precheck
uv run python scripts/agent_migration.py --apply
uv run python scripts/agent_migration.py --precheck
```

预检只读，输出 absent/partial/ready 与差异；未 ready 时退出码为 1。apply 仅创建缺少的完整表，可在部分 DDL 成功后重入。遇到已经存在但字段、索引、外键、CHECK 或默认值不一致的表，严格核验报错，不自动覆盖定义或删除记录。MySQL DDL 隐式提交，迁移不是整体回滚事务。

`001-agent-tables.sql` 是可审阅的增量定义；选择正确数据库后仅用于尚无 Agent 表的旧库。重入使用 Python apply。迁移完成、部署匹配版本并准备独立 Agent Worker 后，解除显式关闭（或设置 `AGENT_ENABLED=true`）；运行要求账户模式和严格 Agent readiness。关闭功能时基础账户 readiness 忽略这些新增表，原提示词流程可运行于尚未升级的数据库；完整迁移后仍可读取已有共享成果。

生成和校验 SQL 不连接数据库：

```powershell
uv run python scripts/agent_migration.py --export
uv run python scripts/agent_migration.py --check-export
uv run python scripts/export_schema.py
uv run python scripts/export_schema.py --check
```

迁移测试使用隔离 MySQL 测试库验证重入、活动 Run 唯一、复合父链外键与部分结构拒绝。不得在个人业务库上执行破坏性测试。发布记录保存脚本版本、执行结果及检查结果；本文件不声明某个实际部署已经升级。
