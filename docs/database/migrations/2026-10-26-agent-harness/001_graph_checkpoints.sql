-- Additive Harness checkpoint tables; select the intended database before execution.

CREATE TABLE agent_graph_checkpoints (
	id BIGINT UNSIGNED NOT NULL, 
	owner_user_id BIGINT UNSIGNED NOT NULL, 
	task_id BIGINT UNSIGNED NOT NULL, 
	run_id BIGINT UNSIGNED NOT NULL, 
	namespace VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	checkpoint_id VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL, 
	parent_id VARCHAR(80), 
	graph_version VARCHAR(40) NOT NULL, 
	checkpoint JSON NOT NULL DEFAULT (JSON_OBJECT()), 
	checkpoint_metadata JSON NOT NULL DEFAULT (JSON_OBJECT()), 
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), 
	PRIMARY KEY (id), 
	CONSTRAINT fk_agent_graph_checkpoints_task FOREIGN KEY(task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_agent_graph_checkpoints_run FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_agent_graph_checkpoints_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_agent_graph_checkpoint UNIQUE (task_id, namespace, checkpoint_id)
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_graph_checkpoints_run ON agent_graph_checkpoints (run_id, id);

CREATE TABLE agent_graph_writes (
	id BIGINT UNSIGNED NOT NULL, 
	owner_user_id BIGINT UNSIGNED NOT NULL, 
	task_id BIGINT UNSIGNED NOT NULL, 
	run_id BIGINT UNSIGNED NOT NULL, 
	namespace VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	checkpoint_id VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL, 
	task_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	write_index INTEGER NOT NULL, 
	task_path VARCHAR(512) NOT NULL, 
	channel VARCHAR(128) NOT NULL, 
	payload JSON NOT NULL DEFAULT (JSON_OBJECT()), 
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), 
	PRIMARY KEY (id), 
	CONSTRAINT fk_agent_graph_writes_task FOREIGN KEY(task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_agent_graph_writes_run FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_agent_graph_writes_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_agent_graph_write UNIQUE (task_id, namespace, checkpoint_id, task_key, write_index)
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_graph_writes_run ON agent_graph_writes (run_id, id);
