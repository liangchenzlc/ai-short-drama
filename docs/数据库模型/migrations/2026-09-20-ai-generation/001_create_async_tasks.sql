-- 创建模型生成任务表，包含当前动作投递信息。
-- MySQL 8.0.21+；先阅读 README.md，按编号执行一次。
-- 已成功执行的文件不要重复执行；不删除或覆盖已有对象。

USE ai_short_drama;
SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SET time_zone = '+00:00';

CREATE TABLE async_tasks (
  id BIGINT UNSIGNED NOT NULL,
  service_type VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL,
  status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'queued',
  idempotency_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL,
  request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  retry_of_id BIGINT UNSIGNED NULL,
  next_action VARCHAR(16) COLLATE utf8mb4_0900_bin NULL,
  next_run_at DATETIME(6) NULL,
  message_status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'pending',
  message_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
  publish_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
  lock_token VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  locked_until DATETIME(6) NULL,
  cancel_requested TINYINT UNSIGNED NOT NULL DEFAULT 0,
  error JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_async_tasks_idempotency (idempotency_key),
  KEY idx_async_tasks_publish (message_status, next_run_at, id),
  KEY idx_async_tasks_lock (message_status, locked_until, id),
  KEY idx_async_tasks_history (service_type, status, created_at, id),
  KEY idx_async_tasks_retry (retry_of_id),
  CONSTRAINT fk_async_tasks_retry FOREIGN KEY (retry_of_id)
    REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_async_tasks_type CHECK (service_type IN ('text','image','video')),
  CONSTRAINT ck_async_tasks_status CHECK (
    status IN ('queued','running','succeeded','failed','cancelled')
  ),
  CONSTRAINT ck_async_tasks_action CHECK (
    next_action IS NULL OR next_action IN ('submit','poll','save')
  ),
  CONSTRAINT ck_async_tasks_message CHECK (
    message_status IN ('pending','publishing','published','idle')
  ),
  CONSTRAINT ck_async_tasks_required CHECK (
    CHAR_LENGTH(TRIM(idempotency_key)) > 0
    AND CHAR_LENGTH(request_hash) = 64 AND message_version > 0
  ),
  CONSTRAINT ck_async_tasks_retry_self CHECK (retry_of_id IS NULL OR retry_of_id <> id),
  CONSTRAINT ck_async_tasks_cancel CHECK (cancel_requested IN (0,1)),
  CONSTRAINT ck_async_tasks_lock_pair CHECK (
    (lock_token IS NULL AND locked_until IS NULL)
    OR (lock_token IS NOT NULL AND locked_until IS NOT NULL)
  ),
  CONSTRAINT ck_async_tasks_terminal_time CHECK (
    (status IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL)
    OR (status IN ('queued','running') AND finished_at IS NULL)
  ),
  CONSTRAINT ck_async_tasks_time CHECK (
    updated_at >= created_at
    AND (started_at IS NULL OR started_at >= created_at)
    AND (finished_at IS NULL OR finished_at >= created_at)
    AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)
  )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='模型生成任务及当前动作投递';
