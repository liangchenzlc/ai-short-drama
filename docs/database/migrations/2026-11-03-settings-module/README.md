# 配置、账号记忆与 MCP 迁移

本次将助手偏好统一为部分 PATCH 和 `settings_version`，长期记忆只归账号，MCP 令牌使用账号动态项目权限。表数仍为 **121**，字段与约束以[完整 SQL](../../schema/schema.mysql8.sql)及 ORM 为准。

## 数据处理

- 助手偏好保留原指令及媒体开关，增加工作台、长期记忆开关与记忆代次；删除用户调用／输出额度、子任务并发、默认媒体数量及旧策略版本。
- Task 删除 `memory_project_ids/media_limits`，保留 `media_usage`；消息增加记忆捕获代次。Run 中已经冻结的内部技术预算及调用收据保留。
- 仅原 `user` 记忆保留为账号记忆。原作品／对话记忆先导出再移出在线库，不自动提升为账号偏好；主体、来源、遗忘规则及修订快照移除作品／对话关联。旧提炼作业取消，关闭期间消息不会在重开后补抽。
- 旧 MCP 令牌先全部撤销，再移除固定项目范围、补创建身份。需要继续使用的客户端必须在新页面重新创建令牌；不会静默扩大旧令牌权限。
- 仅逻辑删除 RunningHub 图片配置并清除图片默认。视频／音频及共用恢复能力保留，已外发图片工作流必须先结算。
- 模型配置和 MCP 令牌增加按账号唯一的 `create_key/create_hash`，创建结果未知时可核对原次请求。

## 执行

脚本使用 `backend/.env` 指向的数据库；执行前确认目标库，停止旧 API、scheduler 与相关 Worker，等待有效 Run 租约退出并核对已外发请求。先保存完整数据库备份，并在隔离数据库预演。

在 `backend/` 执行：

```powershell
uv run python scripts/migrate_account_memory.py --backup-dir ../.runtime/settings-module/development-backup
uv run python scripts/migrate_account_memory.py --backup-dir ../.runtime/settings-module/development-backup --apply
uv run python scripts/export_schema.py
uv run python scripts/export_schema.py --check
```

默认命令只预检。`--apply` 先导出受影响表的 JSON 与原建表语句，再清理数据并执行同目录 `001-settings.sql`，**无需手动重复执行 SQL**。备份目录必须是新目录；生成的备份包含私人数据，不能提交。脚本拒绝有效 Agent 租约及尚未结算的 RunningHub 图片任务，已迁移结构直接退出。

MySQL DDL 会隐式提交，不能依赖事务回滚。若执行中断或需要回退，应保持停写，依据原始完整备份和执行日志恢复，配套恢复旧版本代码；不能直接混用新旧 API／Worker。

## 本地执行记录

2026-11-04 已从真实开发库备份恢复到随机隔离 MySQL 预演，再在当前开发库执行：旧局部记忆 **4 条**已备份移出在线库；有效旧 MCP 令牌 **0 个**、RunningHub 图片配置 **0 个**。没有提升局部记忆或删除业务作品，表数仍为121，全量 SQL 与 ORM 核对一致。

本地完整备份与受影响表导出位于忽略提交的 `.runtime/settings-module/`。新库直接执行最新完整 SQL，不重放本增量。验收结果见[配置模块验收](../../../reviews/2026-11-04-配置模块验收.md)。
