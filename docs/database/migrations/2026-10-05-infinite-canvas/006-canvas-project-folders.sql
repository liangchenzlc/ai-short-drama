-- 本人项目文件夹与画布归属；用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_project_folders (
	user_id BIGINT UNSIGNED NOT NULL, 
	source_key VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	cover_media_id BIGINT UNSIGNED, 
	cover_binary_id BIGINT UNSIGNED, 
	tombstoned_at DATETIME(6), 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_project_folder_key UNIQUE (user_id, source_key), 
	CONSTRAINT ck_canvas_folder_cover CHECK (cover_media_id IS NULL OR cover_binary_id IS NULL), 
	CONSTRAINT ck_canvas_folder_deleted_cover CHECK (tombstoned_at IS NULL OR (cover_media_id IS NULL AND cover_binary_id IS NULL)), 
	CONSTRAINT ck_canvas_folder_name CHECK (CHAR_LENGTH(TRIM(name)) > 0), 
	CONSTRAINT fk_canvas_project_folders_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_project_folders_cover_media_id FOREIGN KEY(cover_media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_project_folders_cover_binary_id FOREIGN KEY(cover_binary_id) REFERENCES canvas_binary_resources (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_project_folders_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_project_folders_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_project_folder_list ON canvas_project_folders (user_id, tombstoned_at, updated_at, id);

CREATE TABLE canvas_project_folder_items (
	id BIGINT UNSIGNED NOT NULL, 
	user_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	folder_key VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_project_folder_item UNIQUE (user_id, canvas_id), 
	CONSTRAINT fk_canvas_project_folder_items_user_id FOREIGN KEY(user_id, folder_key) REFERENCES canvas_project_folders (user_id, source_key) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_project_folder_items_canvas_id FOREIGN KEY(canvas_id) REFERENCES project_canvases (id) ON DELETE CASCADE ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_project_folder_items ON canvas_project_folder_items (user_id, folder_key, canvas_id);
