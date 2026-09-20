-- 建表后只读核对；不插入测试数据。
USE ai_short_drama;

-- 预期三行，列数分别为 19、17、9，且均为 InnoDB。
SELECT t.TABLE_NAME, t.ENGINE, COUNT(c.COLUMN_NAME) AS column_count
FROM information_schema.TABLES AS t
JOIN information_schema.COLUMNS AS c
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
WHERE t.TABLE_SCHEMA = DATABASE()
  AND t.TABLE_NAME IN ('async_tasks', 'ai_generation_records', 'media_assets')
GROUP BY t.TABLE_NAME, t.ENGINE
ORDER BY FIELD(t.TABLE_NAME, 'async_tasks', 'ai_generation_records', 'media_assets');

-- 预期一行：capability_cache、json、YES。
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_model_configs'
  AND COLUMN_NAME = 'capability_cache';

-- 预期五条外键，UPDATE_RULE/DELETE_RULE 均为 RESTRICT。
SELECT TABLE_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME,
       UPDATE_RULE, DELETE_RULE
FROM information_schema.REFERENTIAL_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('async_tasks', 'ai_generation_records', 'media_assets')
ORDER BY TABLE_NAME, CONSTRAINT_NAME;

-- 完整定义供核对唯一索引、CHECK、默认值和外键。
SHOW CREATE TABLE async_tasks;
SHOW CREATE TABLE ai_generation_records;
SHOW CREATE TABLE media_assets;
