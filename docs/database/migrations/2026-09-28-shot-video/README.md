# 分镜视频迁移

新库使用完整 `schema.mysql8.sql`。已完成此前迁移的旧库，在部署新 API/Worker 前执行 `001_shot_video.sql`，或在 backend 目录运行：

```powershell
.venv/Scripts/python.exe scripts/apply_shot_video_migration.py
.venv/Scripts/python.exe scripts/check_db_schema.py
```

新增 `shot_scripts.video_prompt`（默认空，使用分镜默认内容）、`video_settings`（默认 NULL，读取为 720p）；新增 `shot_videos.context_hash` 与引用 `media_files` 的 `first_frame_media_id`（均可空）。旧视频保留，缺少来源摘要时显示需核对。

迁移仅增加字段、索引和约束，不删除或重写作品。脚本按表检查是否缺少这两个字段，已完整执行的表跳过；部分字段存在时停止，需核对实际结构。它不是完整结构比对工具。MySQL DDL 隐式提交，不能承诺跨表原子回滚。发布前保留备份、确认无活动生成任务；迁移后重启 API、调度器和 Worker。

新增字段不能通过直接删列回滚：其中可能已保存用户视频提示词与历史来源。优先向前修复。

全能参考模式沿用本迁移，无额外 DDL：`first_frame_media_id` 保留历史首帧含义；新分镜视频的参考图 ID、排版和输入模式记录在生成任务输入及来源快照中，采用时不填写首帧列。
