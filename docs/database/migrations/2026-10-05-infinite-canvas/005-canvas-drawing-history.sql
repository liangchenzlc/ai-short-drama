-- 整图历史冻结绘图版本；用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_revision_drawing_references (
	revision_id BIGINT UNSIGNED NOT NULL, 
	drawing_version_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_revision_drawing_references_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_drawing_owner FOREIGN KEY(canvas_id, revision_id) REFERENCES canvas_revisions (canvas_id, id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_drawing_version FOREIGN KEY(canvas_id, drawing_version_id) REFERENCES canvas_drawing_versions (canvas_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_revision_drawing UNIQUE (revision_id, drawing_version_id), 
	CONSTRAINT fk_canvas_revision_drawing_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_drawing_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
