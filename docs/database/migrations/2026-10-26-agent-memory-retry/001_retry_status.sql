-- Select the intended current creation-mode database before execution.
-- This expands one CHECK; existing rows, identifiers and waterlines are unchanged.

ALTER TABLE agent_memory_jobs
  DROP CHECK ck_agent_memory_job_status,
  ADD CONSTRAINT ck_agent_memory_job_status CHECK (
    status IN ('pending','running','succeeded','failed','stale','cancelled','abandoned')
    AND provider_state IN ('not_started','sending','received','unknown')
  );
