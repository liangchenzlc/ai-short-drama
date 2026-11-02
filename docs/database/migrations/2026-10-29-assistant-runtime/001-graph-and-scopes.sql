-- 停止 API/Worker 后执行；仅清不可恢复的旧图状态，保留会话、Run、消息及业务成果。
DELETE FROM agent_graph_writes;
DELETE FROM agent_graph_checkpoints;
ALTER TABLE agent_graph_checkpoints ADD INDEX fk_agent_graph_checkpoints_task (task_id);
ALTER TABLE agent_graph_checkpoints
  DROP INDEX uk_agent_graph_checkpoint,
  MODIFY namespace MEDIUMTEXT COLLATE utf8mb4_0900_bin NOT NULL,
  ADD namespace_hash BINARY(32) NOT NULL AFTER namespace,
  DROP COLUMN checkpoint,
  ADD codec VARCHAR(32) NOT NULL AFTER graph_version,
  ADD payload LONGBLOB NOT NULL AFTER codec,
  ADD UNIQUE KEY uk_agent_graph_checkpoint (run_id, namespace_hash, checkpoint_id);
ALTER TABLE agent_graph_writes ADD INDEX fk_agent_graph_writes_task (task_id);
ALTER TABLE agent_graph_writes
  DROP INDEX uk_agent_graph_write,
  MODIFY namespace MEDIUMTEXT COLLATE utf8mb4_0900_bin NOT NULL,
  ADD namespace_hash BINARY(32) NOT NULL AFTER namespace,
  MODIFY task_path TEXT NOT NULL,
  MODIFY payload LONGBLOB NOT NULL,
  ADD codec VARCHAR(32) NOT NULL AFTER channel,
  ADD UNIQUE KEY uk_agent_graph_write (run_id, namespace_hash, checkpoint_id, task_key, write_index);
CREATE TABLE agent_execution_scopes (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	run_id BIGINT UNSIGNED NOT NULL,
	scope_key VARCHAR(96) COLLATE utf8mb4_0900_bin NOT NULL,
	parent_scope_id BIGINT UNSIGNED,
	parent_tool_call_id BIGINT UNSIGNED,
	sandbox_service VARCHAR(255),
	sandbox_id VARCHAR(255),
	workspace_volume VARCHAR(255),
	image_version VARCHAR(255),
	sandbox_status VARCHAR(24),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_scopes_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_scopes_run FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_scopes_parent FOREIGN KEY(parent_scope_id, run_id) REFERENCES agent_execution_scopes (id, run_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_scopes_key UNIQUE (run_id, scope_key),
	CONSTRAINT uk_agent_scopes_run UNIQUE (id, run_id),
	CONSTRAINT ck_agent_scopes_parent CHECK ((scope_key = 'root' AND parent_scope_id IS NULL AND parent_tool_call_id IS NULL) OR (scope_key <> 'root' AND parent_scope_id IS NOT NULL AND parent_tool_call_id IS NOT NULL))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
-- root 的迁移ID使用原Run ID；新范围由应用snowflake生成。
INSERT INTO agent_execution_scopes (id, owner_user_id, run_id, scope_key, created_at, updated_at)
SELECT id, initiated_by, id, 'root', created_at, updated_at FROM agent_runs;
ALTER TABLE agent_turns ADD scope_id BIGINT UNSIGNED NULL AFTER run_id;
UPDATE agent_turns SET scope_id = run_id;
ALTER TABLE agent_turns ADD INDEX fk_agent_turns_run (run_id);
ALTER TABLE agent_turns MODIFY scope_id BIGINT UNSIGNED NOT NULL,
  DROP INDEX uk_agent_turns_number,
  ADD UNIQUE KEY uk_agent_turns_number (scope_id, turn_no),
  ADD CONSTRAINT fk_agent_turns_scope FOREIGN KEY (scope_id, run_id)
    REFERENCES agent_execution_scopes (id, run_id) ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE agent_tool_calls ADD scope_id BIGINT UNSIGNED NULL AFTER run_id;
UPDATE agent_tool_calls SET scope_id = run_id;
ALTER TABLE agent_tool_calls MODIFY scope_id BIGINT UNSIGNED NOT NULL,
  ADD UNIQUE KEY uk_agent_calls_run (id, run_id),
  ADD CONSTRAINT fk_agent_calls_scope FOREIGN KEY (scope_id, run_id)
    REFERENCES agent_execution_scopes (id, run_id) ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE agent_operations ADD scope_id BIGINT UNSIGNED NULL AFTER run_id;
UPDATE agent_operations SET scope_id = run_id;
ALTER TABLE agent_operations MODIFY scope_id BIGINT UNSIGNED NOT NULL,
  DROP INDEX uk_agent_operations_active, DROP COLUMN active_task_id,
  ADD CONSTRAINT fk_agent_operations_scope FOREIGN KEY (scope_id, run_id)
    REFERENCES agent_execution_scopes (id, run_id) ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE agent_execution_scopes ADD CONSTRAINT fk_agent_scopes_parent_call FOREIGN KEY(parent_tool_call_id, run_id) REFERENCES agent_tool_calls (id, run_id) ON DELETE RESTRICT ON UPDATE RESTRICT;
