-- 私人供应商正文增量与断线游标；用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_task_text_deltas (
	task_binding_id BIGINT UNSIGNED NOT NULL, 
	generation_record_id BIGINT UNSIGNED NOT NULL, 
	sequence INTEGER UNSIGNED NOT NULL, 
	content MEDIUMTEXT NOT NULL, 
	byte_count INTEGER UNSIGNED NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_task_text_deltas_project_id FOREIGN KEY(project_id, canvas_id, task_binding_id) REFERENCES canvas_task_bindings (project_id, canvas_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_text_sequence UNIQUE (task_binding_id, sequence), 
	CONSTRAINT ck_canvas_text_delta CHECK (sequence BETWEEN 1 AND 4096 AND byte_count BETWEEN 1 AND 65536 AND OCTET_LENGTH(content) = byte_count), 
	CONSTRAINT fk_canvas_task_text_deltas_generation_record_id FOREIGN KEY(generation_record_id) REFERENCES ai_generation_records (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_task_text_deltas_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_task_text_deltas_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_text_author ON canvas_task_text_deltas (created_by, expires_at);

CREATE INDEX idx_canvas_text_expiry ON canvas_task_text_deltas (expires_at, id);
