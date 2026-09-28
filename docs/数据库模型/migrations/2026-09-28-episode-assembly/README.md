# 成片合成与导出

本迁移为已有库增加 `episode_assemblies`、`episode_assembly_clips`、`episode_render_jobs`，并为 `media_files` 增加 `video_metadata`。不删除或重写既有业务数据。

升级前先完成分镜视频迁移。停止更新中的 API/调度器后，在 backend 目录执行：

```powershell
.venv/Scripts/python scripts/apply_assembly_migration.py
.venv/Scripts/python scripts/check_db_schema.py
```

Python 升级程序检查已有表、字段和约束，允许在 DDL 部分执行后重入。直接执行 `001_episode_assembly.sql` 仅适用于尚未应用本次迁移的库。MySQL DDL 不支持整批回滚，生产环境应先备份并在维护窗口执行。

`schema.mysql8.sql` 是全项目空库建表入口，包含全部 24 张表和本次全部变更。不能用它代替已有库迁移。单元测试核对字段类型、默认值、可空性、外键、检查约束和索引；集成测试在随机命名的独立库中从该文件创建所有表。

成片草稿通过 `row_version` 乐观锁保存。片段引用 `media_files`，不修改 `shot_videos`。成片任务的 `snapshot` 冻结片段顺序、媒体定位、裁剪、静音和输出参数。`retry_of_id` 由服务校验同一草稿归属，不设置循环外键。当前采用成片存为 `episode_assemblies.current_media_id`。
