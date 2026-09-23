# 五阶段生产工作流迁移

新库使用[完整建表脚本](../../schema.mysql8.sql)，不执行本目录。旧库先完成[分集写作迁移](../2026-09-21-episode-writing/README.md)。本目录只交付迁移，不代表任何业务库已经执行。先对数据库元数据和 `episodes`、`shot_scripts`、`shot_assets`、`shot_images`、`shot_videos`、`script_shot_records`、`media_recycle_bin`、`assets`、`media_files` 做可恢复备份，并记录备份位置与时间。

项目统一要求 MySQL 8.0.21+；本目录预检仅检查 CHECK 可用的较低版本门槛，仍须人工核对完整版本。迁移窗口必须暂停 API 写入、Worker 的业务保存与媒体采用；读取可以继续。使用同一个 `mysql` 客户端连接依次 `SOURCE` 以下 SQL，使 `000` 创建的临时基线表保留到 `005`：

1. `000_precheck.sql`。确认 `version_ok=1`，所有问题计数为0，再继续。
2. `001_episode_storyboard.sql`。
3. `002_asset_persistence.sql`。
4. `003_shot_image_context.sql`。
5. 在仓库 `backend` 目录通过进程环境安全提供 `DATABASE_URL` 后运行 `uv run python ../docs/数据库模型/migrations/2026-09-21-production-workflow/004_backfill_asset_candidates.py` 预览（工具直接读取环境变量，不自动读取 `.env`）；数量正确后增加 `--apply`。脚本分批提交、已有组合跳过，坏媒体引用会停止。
6. 回到原 `mysql` 连接执行 `005_verify.sql`。

SQL脚本逐项查询 `information_schema`，中断后可从对应文件重入；已存在的列、约束和索引不会重建，也不会重置版本或覆盖新业务数据。Python回填按 `(asset_id,media_id)` 幂等。若 `000` 与 `005` 不在同一连接，临时基线不存在，必须从 `000` 重新开始验证流程。

验证成功后部署包含新 Domain/API/Worker 的应用，再恢复 Worker 保存与 API 写入。恢复后先做只读健康检查，再验收分镜列表、素材候选和历史媒体读取。

MySQL DDL不保证整个目录原子回滚。产生新归档、共享引用、候选或采用上下文后，不要删除新列、候选表或重建旧的 `uk_shots_episode_position`。发生问题时保持写入暂停，保留备份与当前库，修正迁移或应用并向前恢复；只有在尚未部署新应用、没有任何新格式写入且已核对备份时，才由数据库负责人制定人工回退步骤。

本阶段完成后继续检查[分镜时长与原文依据迁移](../2026-09-22-storyboard-prompts/README.md)。完整顺序见[迁移入口](../README.md)。
