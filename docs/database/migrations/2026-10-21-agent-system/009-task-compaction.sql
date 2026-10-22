-- Explicit offline migration for the account assistant.

ALTER TABLE agent_memory_jobs DROP CHECK ck_agent_memory_job_kind, ADD CONSTRAINT ck_agent_memory_job_kind CHECK (kind IN ('summary','extract','task_state','purge') AND from_seq > 0 AND to_seq >= from_seq);
