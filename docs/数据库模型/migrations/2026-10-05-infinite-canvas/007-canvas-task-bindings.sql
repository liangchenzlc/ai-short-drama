-- 私人画布任务出处、媒体引用和产物；用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_task_bindings (
	initiated_by BIGINT UNSIGNED NOT NULL, 
	async_task_id BIGINT UNSIGNED NOT NULL, 
	node_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	source_node_key VARCHAR(128) COLLATE utf8mb4_0900_bin, 
	client_operation_id VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	source_snapshot JSON NOT NULL, 
	context_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_task_bindings_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_task_async UNIQUE (async_task_id), 
	CONSTRAINT uk_canvas_task_operation UNIQUE (initiated_by, client_operation_id), 
	CONSTRAINT uk_canvas_task_scope UNIQUE (project_id, canvas_id, id), 
	CONSTRAINT ck_canvas_task_required CHECK (CHAR_LENGTH(TRIM(node_key)) > 0 AND CHAR_LENGTH(TRIM(client_operation_id)) > 0 AND CHAR_LENGTH(request_hash) = 64 AND CHAR_LENGTH(context_hash) = 64), 
	CONSTRAINT fk_canvas_task_bindings_initiated_by FOREIGN KEY(initiated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_task_bindings_async_task_id FOREIGN KEY(async_task_id) REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_task_bindings_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_task_bindings_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_task_author ON canvas_task_bindings (initiated_by, canvas_id, created_at, id);

CREATE TABLE canvas_results (
	task_binding_id BIGINT UNSIGNED NOT NULL, 
	result_index INTEGER UNSIGNED NOT NULL, 
	kind VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL, 
	content_json JSON NOT NULL, 
	media_id BIGINT UNSIGNED, 
	attachment_status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL, 
	attachment_receipt_id BIGINT UNSIGNED, 
	attached_at DATETIME(6), 
	row_version BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_results_project_id FOREIGN KEY(project_id, canvas_id, task_binding_id) REFERENCES canvas_task_bindings (project_id, canvas_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_result_output UNIQUE (task_binding_id, result_index), 
	CONSTRAINT ck_canvas_result_kind CHECK (kind IN ('text','image','video','audio')), 
	CONSTRAINT ck_canvas_result_media CHECK ((kind = 'text' AND media_id IS NULL) OR (kind <> 'text' AND media_id IS NOT NULL)), 
	CONSTRAINT ck_canvas_result_attachment CHECK ((attachment_status = 'detached' AND attachment_receipt_id IS NULL AND attached_at IS NULL) OR (attachment_status = 'attached' AND attachment_receipt_id IS NOT NULL AND attached_at IS NOT NULL)), 
	CONSTRAINT ck_canvas_result_version CHECK (row_version > 0), 
	CONSTRAINT fk_canvas_results_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_results_attachment_receipt_id FOREIGN KEY(attachment_receipt_id) REFERENCES canvas_write_receipts (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_results_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_results_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_result_author ON canvas_results (created_by, canvas_id, attachment_status, id);

CREATE INDEX idx_canvas_result_media ON canvas_results (media_id);

CREATE TABLE canvas_task_media_references (
	task_binding_id BIGINT UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED NOT NULL, 
	`role` VARCHAR(64) COLLATE utf8mb4_0900_bin NOT NULL, 
	ordinal INTEGER UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_task_media_references_project_id FOREIGN KEY(project_id, canvas_id, task_binding_id) REFERENCES canvas_task_bindings (project_id, canvas_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_task_media_slot UNIQUE (task_binding_id, `role`, ordinal), 
	CONSTRAINT ck_canvas_task_media_role CHECK (CHAR_LENGTH(TRIM(role)) > 0), 
	CONSTRAINT fk_canvas_task_media_references_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_task_media_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_task_media_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_task_media ON canvas_task_media_references (media_id);
