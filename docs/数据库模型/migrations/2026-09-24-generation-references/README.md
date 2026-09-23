# 持久化生成参考图片

已有数据库在部署本次 API、调度器和 Worker 前执行 `001_add_reference_images.sql`。新库直接使用完整 schema，不重复执行增量文件。

## 执行步骤

1. 核对目标库，备份 assets、shot_scripts 及完整数据库；暂停 API 和后台写入，保留现有任务状态。
2. 查 information_schema.columns，确认两表尚无 reference_media_ids；若某列已存在，先核对 JSON、NOT NULL、默认 JSON_ARRAY()，只执行缺失列对应语句。脚本不是自动重入迁移。
3. 在已选定的数据库执行两条 ALTER。仅新增默认空数组，不改原正文、候选、采用图片、row_version 或 storyboard_version。MySQL DDL 隐式提交，若第二条失败，保留第一条并在修复后只执行缺失项。
4. 核验两列定义、现有行数及原内容/版本，新旧行参考列表均为数组。部署新版本 API/Worker 后恢复写入。

新增输入图保存在 media_files 与 MinIO；从参考区移除仅解除引用，历史请求仍能读取原文件。不要为回滚删除已有参考列或媒体文件，优先向前修复。

## 验证

在 backend 目录执行 `python scripts/run_integration.py tests/integration/test_generation_reference_migration.py -q`。fixture 使用随机隔离库验证旧数据保留及完整 schema 一致性，不升级实际业务库。运行时不会自动执行此迁移。
