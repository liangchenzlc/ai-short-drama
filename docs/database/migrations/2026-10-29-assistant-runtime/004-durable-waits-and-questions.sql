-- Keep all accepted native tasks and historical orchestration receipts.
ALTER TABLE agent_operations
  ADD COLUMN wait_state JSON NULL AFTER arguments,
  DROP CHECK ck_agent_operations_status,
  ADD CONSTRAINT ck_agent_operations_status CHECK (status IN ('running','waiting_media','waiting_input','reconciling','succeeded','failed','conflict')),
  ADD CONSTRAINT ck_agent_operations_wait_state CHECK (wait_state IS NULL OR (JSON_TYPE(wait_state) = 'OBJECT' AND JSON_UNQUOTE(JSON_EXTRACT(wait_state, '$.kind')) IN ('generation','user_input')));

ALTER TABLE agent_tasks
  DROP CHECK ck_agent_tasks_status,
  DROP CHECK ck_agent_tasks_phase,
  ADD CONSTRAINT ck_agent_tasks_status CHECK (status IN ('queued','running','waiting_media','waiting_input','succeeded','stopped','paused_error','paused_budget')),
  ADD CONSTRAINT ck_agent_tasks_phase CHECK (phase IN ('deciding','tool_running','waiting_media','waiting_input','settling_result','reconciling','at_boundary','terminal'));

ALTER TABLE agent_runs
  DROP CHECK ck_agent_runs_status,
  ADD CONSTRAINT ck_agent_runs_status CHECK (status IN ('queued','running','waiting_generation','waiting_input','waiting_review','succeeded','failed','cancelled')),
  MODIFY COLUMN active_conversation_id BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN status IN ('queued','running','waiting_generation','waiting_input','waiting_review') THEN conversation_id ELSE NULL END) STORED;

ALTER TABLE agent_tool_calls
  DROP CHECK ck_agent_tool_calls_status,
  ADD CONSTRAINT ck_agent_tool_calls_status CHECK (status IN ('prepared','waiting_review','ready','running','waiting_generation','waiting_input','succeeded','failed','rejected','cancelled'));

ALTER TABLE agent_messages
  DROP CHECK ck_agent_messages_kind,
  ADD CONSTRAINT ck_agent_messages_kind CHECK (kind IN ('reply','narration','notice','tool_answer'));
