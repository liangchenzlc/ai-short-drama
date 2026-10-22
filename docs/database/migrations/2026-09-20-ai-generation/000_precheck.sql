-- 执行前只读检查。不会自动阻止后续文件，请按 README.md 核对结果。
USE ai_short_drama;

SELECT DATABASE() AS selected_database,
       VERSION() AS mysql_version,
       @@SESSION.sql_mode AS session_sql_mode;

-- 应有 ai_model_configs、media_files 两张 InnoDB 表。
SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('ai_model_configs', 'media_files')
ORDER BY TABLE_NAME;

-- 两张依赖表的 id 均应是 bigint unsigned、非空、主键。
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('ai_model_configs', 'media_files')
  AND COLUMN_NAME = 'id'
ORDER BY TABLE_NAME;

-- 首次执行时以下两个结果集应为空。
SELECT TABLE_NAME AS already_existing_new_table
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('async_tasks', 'ai_generation_records', 'media_assets')
ORDER BY TABLE_NAME;

SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_model_configs'
  AND COLUMN_NAME = 'capability_cache';
