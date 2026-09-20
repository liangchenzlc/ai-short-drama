-- 将旧版任务 unknown 合并为 failed；执行前暂停 API、调度器和 Worker。
-- 仅修改任务状态及对应约束，不删除任务、调用记录或资产。
-- 模型调用记录中的 unknown 是内部受理证据，保持不变以阻止重复生成。
USE ai_short_drama;
SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SET time_zone = '+00:00';

START TRANSACTION;
UPDATE async_tasks
SET status = 'failed',
    finished_at = GREATEST(UTC_TIMESTAMP(6), updated_at, created_at, COALESCE(started_at, created_at)),
    updated_at = GREATEST(UTC_TIMESTAMP(6), updated_at, created_at, COALESCE(started_at, created_at)),
    next_action = CASE
      WHEN JSON_UNQUOTE(JSON_EXTRACT(error, '$.code')) = 'message_delivery_unknown'
      THEN next_action ELSE NULL END,
    next_run_at = NULL,
    message_status = 'idle',
    message_version = message_version + 1,
    lock_token = NULL,
    locked_until = NULL
WHERE status = 'unknown';
COMMIT;

ALTER TABLE async_tasks
  DROP CHECK ck_async_tasks_status,
  DROP CHECK ck_async_tasks_terminal_time,
  ADD CONSTRAINT ck_async_tasks_status CHECK (
    status IN ('queued','running','succeeded','failed','cancelled')
  ),
  ADD CONSTRAINT ck_async_tasks_terminal_time CHECK (
    (status IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL)
    OR (status IN ('queued','running') AND finished_at IS NULL)
  );
