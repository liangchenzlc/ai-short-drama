-- 已保存绘图、不可变版本及媒体保护；用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_drawings (
	source_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	archived_at DATETIME(6), 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_drawings_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_drawing_key UNIQUE (canvas_id, source_key), 
	CONSTRAINT uk_canvas_drawing_id UNIQUE (canvas_id, id), 
	CONSTRAINT ck_canvas_drawing_version CHECK (row_version > 0), 
	CONSTRAINT ck_canvas_drawing_key CHECK (CHAR_LENGTH(TRIM(source_key)) > 0), 
	CONSTRAINT fk_canvas_drawings_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_drawings_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_drawing_active ON canvas_drawings (canvas_id, archived_at, updated_at);

CREATE TABLE canvas_drawing_versions (
	drawing_id BIGINT UNSIGNED NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	document_json JSON NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_drawing_versions_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_drawing_version_parent FOREIGN KEY(canvas_id, drawing_id) REFERENCES canvas_drawings (canvas_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_drawing_version UNIQUE (drawing_id, row_version), 
	CONSTRAINT uk_canvas_drawing_version_id UNIQUE (canvas_id, id), 
	CONSTRAINT ck_canvas_drawing_snapshot_version CHECK (row_version > 0), 
	CONSTRAINT fk_canvas_drawing_versions_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_drawing_versions_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_drawing_media_references (
	drawing_version_id BIGINT UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_drawing_media_references_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_drawing_media_version FOREIGN KEY(canvas_id, drawing_version_id) REFERENCES canvas_drawing_versions (canvas_id, id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_drawing_media UNIQUE (drawing_version_id, media_id), 
	CONSTRAINT fk_canvas_drawing_media_references_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_drawing_media_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_drawing_media_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
