-- 创建模型调用记录表，依赖 async_tasks、ai_model_configs。
-- MySQL 8.0.21+；先阅读 README.md，按编号执行一次。
-- 已成功执行的文件不要重复执行；不删除或覆盖已有对象。

USE ai_short_drama;
SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SET time_zone = '+00:00';

CREATE TABLE ai_generation_records (
  id BIGINT UNSIGNED NOT NULL,
  task_id BIGINT UNSIGNED NOT NULL,
  call_no INT UNSIGNED NOT NULL,
  config_id BIGINT UNSIGNED NOT NULL,
  config_snapshot JSON NOT NULL,
  request_data JSON NOT NULL,
  credential_cipher TEXT NULL,
  adapter VARCHAR(64) COLLATE utf8mb4_0900_bin NULL,
  provider_task_id VARCHAR(255) COLLATE utf8mb4_0900_bin NULL,
  status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'prepared',
  text_content MEDIUMTEXT NULL,
  response_data JSON NULL,
  error JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ai_records_call (task_id, call_no),
  KEY idx_ai_records_config_time (config_id, created_at, id),
  KEY idx_ai_records_provider_task (provider_task_id),
  CONSTRAINT fk_ai_records_task FOREIGN KEY (task_id)
    REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_ai_records_config FOREIGN KEY (config_id)
    REFERENCES ai_model_configs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_ai_records_call_no CHECK (call_no > 0),
  CONSTRAINT ck_ai_records_status CHECK (
    status IN ('prepared','sent','succeeded','failed','unknown')
  ),
  CONSTRAINT ck_ai_records_time CHECK (
    updated_at >= created_at
    AND (started_at IS NULL OR started_at >= created_at)
    AND (finished_at IS NULL OR finished_at >= created_at)
    AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)
  )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='模型调用记录与文本结果';
