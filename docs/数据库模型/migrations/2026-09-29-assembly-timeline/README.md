# 时间轴剪辑升级

从 2026-09-28 合成结构升级，保留现有草稿、媒体和历史导出。新库直接执行完整 `schema.mysql8.sql`，无需再执行本迁移。

已有库在 `backend/` 执行：

```powershell
.venv/Scripts/python.exe scripts/apply_timeline_migration.py
.venv/Scripts/python.exe scripts/check_db_schema.py
```

脚本要求没有 queued/running 合成任务，先将三张合成表的数据备份到 `.runtime/schema-backups/timeline-*.json`，然后按实际字段、索引和约束逐项升级，允许中断后重复执行。部署期间应暂停编辑入口，升级完成后一起重启 API、调度器和 render Worker。MySQL DDL 非事务性，不要将备份文件误认为自动回滚程序。

变更：

- `episode_assemblies.last_edit_receipt`：最近一次保存的请求标识和内容摘要，用于响应丢失后的幂等重试。
- `episode_assembly_clips.client_key`：新片段的稳定 UUID；原有片段继续使用原 ID。新增 `(assembly_id, client_key)` 唯一键。
- `episode_assembly_clips.removed`：移出轨道的片段保留来源和裁剪，支持本次会话撤销，并记录该分镜已同步。轨道最多 300 个活动片段。
- 移除 `(assembly_id, shot_id)` 唯一键，增加同字段普通索引。一个分镜可以在时间轴上出现多次。
- `episode_render_jobs.kind` 增加 `preview`。预览不能设为当前正式成片。

无需创建额外来源表：保留的片段记录承担来源同步标记，GET 返回去重后的 `sources` 素材列表。媒体替换只在明确同步时执行，并保留分割与裁剪范围；超出新视频长度的片段需手动修正。

不能直接回退到一对一片段的旧 API：存在分割数据时恢复旧唯一键会失败。回退需要先导出当前草稿并制定数据恢复方案。历史 version 1 导出快照仍由渲染器兼容。
