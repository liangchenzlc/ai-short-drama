-- 已有011库允许无图片的私人分镜执行；用canvas_migration.py --apply核验并安全重入。
ALTER TABLE canvas_tool_runs MODIFY COLUMN source_media_id BIGINT UNSIGNED NULL;
