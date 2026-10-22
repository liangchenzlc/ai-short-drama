-- 已安装前版画布表的旧库增量；优先用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_resource_copy_sources (
	id BIGINT UNSIGNED NOT NULL, 
	upload_id BIGINT UNSIGNED NOT NULL, 
	original_resource_id BIGINT UNSIGNED NOT NULL, 
	source_media_id BIGINT UNSIGNED, 
	source_binary_id BIGINT UNSIGNED, 
	snapshot_json JSON NOT NULL, 
	released_at DATETIME(6), 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_copy_upload UNIQUE (upload_id), 
	CONSTRAINT ck_canvas_copy_source_pin CHECK (original_resource_id > 0 AND ((released_at IS NULL AND ((source_media_id IS NULL) <> (source_binary_id IS NULL))) OR (released_at IS NOT NULL AND source_media_id IS NULL AND source_binary_id IS NULL))), 
	CONSTRAINT fk_canvas_resource_copy_sources_upload_id FOREIGN KEY(upload_id) REFERENCES canvas_resource_uploads (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_copy_sources_source_media_id FOREIGN KEY(source_media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_copy_sources_source_binary_id FOREIGN KEY(source_binary_id) REFERENCES canvas_binary_resources (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_copy_origin ON canvas_resource_copy_sources (original_resource_id);

ALTER TABLE canvas_resource_uploads DROP CHECK ck_canvas_upload_mode, ADD CONSTRAINT ck_canvas_upload_mode CHECK (mode IN ('multipart','chunked','copy'));
