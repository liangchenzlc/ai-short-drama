# Skill 管理迁移

删除 `agent_skill_settings.invocation_mode`，同名 CHECK 收紧为 `row_version > 0`。不新增表，不修改启停、身份、设置版本、历史内容、对象存储或任务快照。历史 JSON 中的旧字段只作审计数据，不再参与判断。

1. 在隔离库预演。执行 [preflight.sql](preflight.sql)，确认目标库、MySQL 8.0.21+、原列及 `ck_agent_skill_setting_values` 的定义完全匹配，违规记录为零。保存设置表及完整业务库、MinIO 的可恢复备份；禁止打印连接密码。
2. 停止旧 API、scheduler、Agent Worker 的写入，等待在途事务完成。备份中保留每条设置的 `id/owner_user_id/skill_key/invocation_mode/row_version`，并记录设置行、历史版本行和任务引用的数量与摘要。
3. 执行 [001_drop_invocation_mode.sql](001_drop_invocation_mode.sql)。该单条原子 DDL 先移除依赖 CHECK，再删除列并重建版本约束。MySQL DDL 自动提交，不能用事务回滚代替恢复方案。
4. 重跑预检：旧列不存在，CHECK 只包含 `row_version > 0`，`enabled` 及身份约束仍在；原有行、历史版本及任务引用摘要不变。重新执行 `backend/scripts/export_schema.py --check`，运行 Skill 的 MySQL 测试。
5. API、Worker 与前端同版启动。不可让旧进程继续访问已删除列；在途任务仍使用自己的冻结包及引擎，不回填历史快照。

**重入规则：** 已无旧列且版本 CHECK、其他字段和约束均与新 ORM 一致时，只核验并跳过 DDL；不可直接重跑 ALTER。出现“旧列不存在但 CHECK 不正确”或未知列定义时停止，先排查漂移，不把重复执行错误当成功。

**恢复：** 停止新版写入，执行 [002_restore_invocation_mode.sql](002_restore_invocation_mode.sql)，从迁移前备份按设置身份恢复旧列值，再核验其他字段未被覆盖。迁移后新增设置没有旧取值，使用旧版默认 `auto`；不得恢复整张旧表覆盖迁移后的启停与版本。新版本内容和历史资源继续保留，不能删除任务使用的版本。备份不可用时无法声称已恢复原自动／手动偏好。

本机执行记录与验证结果在本次实现验收中记录；其他部署必须独立执行预检、备份和迁移，应用启动不自动迁移。

2026-10-26 Skill 管理实施已接续执行本机完整迁移：确认无活动 Agent 任务，停止 API、scheduler 和 Agent Worker，备份业务库、个人设置原值及 25 份版本资源后删除旧列，再启动新版进程。2 条设置、20 个历史版本、19 个任务、24 个输入快照及 23 个运行记录的其余内容摘要一致，结构预检为 `ready`。备份位于本机 `.runtime/skill-implementation/20261026T114331Z/`，详情见 [Skill 管理页验收](../../../reviews/2026-10-26-Skill管理页验收.md)。下面的中间状态记录现已完成接续，仅作历史背景。

2026-10-26 Harness 验收收尾的兼容记录：本机仅将版本 CHECK 对齐 `row_version > 0`，保留旧列及两条设置原值，以恢复当前代码的结构预检；尚未执行删列 DDL，不能将 `ready` 当作本迁移全部完成。后续清理须核对这一已知中间状态并备份旧值，再执行原子删列；不要把中间状态误报为完整升级。记录见[Harness 前三批验收](../../../reviews/2026-10-26-Harness前三批可靠性与上下文优化验收.md)。
