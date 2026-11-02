-- 仅由 backend/scripts/agent_runtime_migration.py --apply 在离线预检查、审计归档后执行。
-- 起点为001–005已完成的122表；每条DDL隐式提交，脚本核验已知阶段后才能续跑。
ALTER TABLE agent_tool_calls
  DROP CHECK ck_agent_tool_calls_review_actor,
  DROP CHECK ck_agent_tool_calls_review_decision,
  DROP CHECK ck_agent_tool_calls_review_pair,
  DROP CHECK ck_agent_tool_calls_review_payload,
  DROP CHECK ck_agent_tool_calls_review_pending,
  DROP CHECK ck_agent_tool_calls_numbers,
  DROP CHECK ck_agent_tool_calls_time,
  DROP CHECK ck_agent_tool_calls_status,
  DROP FOREIGN KEY fk_agent_tool_calls_reviewer,
  DROP COLUMN review_payload,
  DROP COLUMN review_hash,
  DROP COLUMN review_version,
  DROP COLUMN review_decision,
  DROP COLUMN reviewed_by,
  DROP COLUMN reviewed_at,
  ADD CONSTRAINT ck_agent_tool_calls_numbers CHECK (call_index > 0),
  ADD CONSTRAINT ck_agent_tool_calls_time CHECK (updated_at >= created_at),
  ADD CONSTRAINT ck_agent_tool_calls_status CHECK (status IN ('prepared','ready','running','waiting_generation','waiting_input','succeeded','failed','rejected','cancelled'));

ALTER TABLE agent_operations
  DROP CHECK ck_agent_operations_evidence,
  DROP INDEX idx_agent_operations_evidence,
  DROP COLUMN result_digest,
  DROP COLUMN evidence_kind;

ALTER TABLE agent_runs
  DROP CHECK ck_agent_runs_status,
  MODIFY COLUMN active_conversation_id BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN status IN ('queued','running','waiting_generation','waiting_input') THEN conversation_id ELSE NULL END) STORED NULL,
  ADD CONSTRAINT ck_agent_runs_status CHECK (status IN ('queued','running','waiting_generation','waiting_input','succeeded','failed','cancelled'));

ALTER TABLE agent_memory_jobs
  DROP CHECK ck_agent_memory_job_kind,
  ADD CONSTRAINT ck_agent_memory_job_kind CHECK (kind IN ('summary','extract','purge') AND from_seq > 0 AND to_seq >= from_seq);

DROP TABLE agent_task_requirements;
