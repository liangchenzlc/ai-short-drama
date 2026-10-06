-- 无限画布核心存储；旧库使用 canvas_migration.py --apply 安全重入。
ALTER TABLE projects ADD COLUMN workspace_mode VARCHAR(16) NOT NULL DEFAULT 'standard';

ALTER TABLE projects ADD CONSTRAINT ck_projects_workspace_mode CHECK (workspace_mode IN ('standard','infinite_canvas'));

CREATE TABLE canvas_library_folders (
	user_id BIGINT UNSIGNED NOT NULL, 
	name VARCHAR(40) NOT NULL, 
	name_key VARCHAR(40) COLLATE utf8mb4_0900_bin NOT NULL, 
	position INTEGER UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_library_folder_name UNIQUE (user_id, name_key), 
	CONSTRAINT fk_canvas_library_folders_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_folders_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_folders_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_library_folder_order ON canvas_library_folders (user_id, position, id);

CREATE TABLE canvas_resource_deletions (
	user_id BIGINT UNSIGNED NOT NULL, 
	resource_id BIGINT UNSIGNED NOT NULL, 
	upload_id BIGINT UNSIGNED NOT NULL, 
	idempotency_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	storage_locator VARCHAR(700) COLLATE utf8mb4_0900_bin NOT NULL, 
	chunk_count INTEGER UNSIGNED NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	attempts INTEGER UNSIGNED NOT NULL, 
	next_attempt_at DATETIME(6) NOT NULL, 
	completed_at DATETIME(6), 
	error_code VARCHAR(64), 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_deletion_resource UNIQUE (resource_id), 
	CONSTRAINT uk_canvas_deletion_upload UNIQUE (upload_id), 
	CONSTRAINT uk_canvas_deletion_identity UNIQUE (user_id, idempotency_hash), 
	CONSTRAINT ck_canvas_deletion_status CHECK (status IN ('pending','completed','retained')), 
	CONSTRAINT ck_canvas_deletion_completion CHECK ((status = 'pending' AND completed_at IS NULL) OR (status IN ('completed','retained') AND completed_at IS NOT NULL)), 
	CONSTRAINT fk_canvas_resource_deletions_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_deletions_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_deletions_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_deletion_due ON canvas_resource_deletions (status, next_attempt_at, id);

CREATE TABLE canvas_workspace_user_states (
	user_id BIGINT UNSIGNED NOT NULL, 
	preferences_json JSON NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_workspace_user UNIQUE (user_id), 
	CONSTRAINT ck_canvas_workspace_user_version CHECK (row_version > 0), 
	CONSTRAINT fk_canvas_workspace_user_states_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_workspace_user_states_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_workspace_user_states_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_binary_resources (
	published_at DATETIME(6), 
	resource_kind VARCHAR(16) NOT NULL, 
	mime_type VARCHAR(127) COLLATE utf8mb4_0900_bin NOT NULL, 
	storage_locator VARCHAR(700) COLLATE utf8mb4_0900_bin NOT NULL, 
	original_name VARCHAR(255) NOT NULL, 
	byte_size BIGINT UNSIGNED NOT NULL, 
	checksum_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	scope_user_id BIGINT UNSIGNED, 
	project_id BIGINT UNSIGNED, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_binary_locator UNIQUE (storage_locator), 
	CONSTRAINT ck_canvas_binary_scope CHECK ((scope_user_id IS NULL) <> (project_id IS NULL)), 
	CONSTRAINT ck_canvas_binary_kind CHECK (resource_kind = 'file' AND byte_size > 0), 
	CONSTRAINT ck_canvas_binary_checksum CHECK (CHAR_LENGTH(checksum_sha256) = 64), 
	CONSTRAINT fk_canvas_binary_resources_scope_user_id FOREIGN KEY(scope_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_binary_resources_project_id FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_binary_resources_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_binary_resources_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_creation_attempts (
	user_id BIGINT UNSIGNED NOT NULL, 
	idempotency_key VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	operation_kind VARCHAR(32) NOT NULL, 
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	request_json JSON NOT NULL, 
	source_key VARCHAR(64) COLLATE utf8mb4_0900_bin NOT NULL, 
	target_project_id BIGINT UNSIGNED, 
	status VARCHAR(16) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	result_json JSON, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_creation_identity UNIQUE (user_id, idempotency_key), 
	CONSTRAINT uk_canvas_creation_source_key UNIQUE (user_id, source_key), 
	CONSTRAINT uk_canvas_creation_owner UNIQUE (id, user_id), 
	CONSTRAINT ck_canvas_creation_destination CHECK ((operation_kind = 'canvas.workspace.create' AND target_project_id IS NULL) OR (operation_kind = 'canvas.create' AND target_project_id IS NOT NULL)), 
	CONSTRAINT ck_canvas_creation_result CHECK ((status = 'pending' AND result_json IS NULL) OR (status = 'ready' AND result_json IS NOT NULL)), 
	CONSTRAINT ck_canvas_creation_identity CHECK (CHAR_LENGTH(request_hash) = 64 AND CHAR_LENGTH(source_key) > 0), 
	CONSTRAINT fk_canvas_creation_attempts_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_creation_attempts_target_project_id FOREIGN KEY(target_project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_creation_attempts_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_creation_attempts_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_creation_expiry ON canvas_creation_attempts (status, expires_at);

CREATE TABLE canvas_library_assets (
	user_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED, 
	source_key VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	payload_json JSON NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_library_asset UNIQUE (user_id, source_key), 
	CONSTRAINT ck_canvas_library_kind CHECK (kind IN ('text','image','video','audio','model','entity')), 
	CONSTRAINT fk_canvas_library_assets_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_assets_project_id FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_assets_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_assets_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_library_user ON canvas_library_assets (user_id, updated_at, id);

CREATE TABLE project_canvases (
	project_id BIGINT UNSIGNED NOT NULL, 
	source_key VARCHAR(64) COLLATE utf8mb4_0900_bin NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	position INTEGER UNSIGNED NOT NULL, 
	schema_version INTEGER UNSIGNED NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	properties_json JSON NOT NULL, 
	archived_at DATETIME(6), 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_project_id UNIQUE (project_id, id), 
	CONSTRAINT uk_canvas_source_key UNIQUE (source_key), 
	CONSTRAINT ck_canvas_version CHECK (row_version > 0 AND schema_version > 0), 
	CONSTRAINT ck_canvas_title CHECK (CHAR_LENGTH(TRIM(title)) > 0), 
	CONSTRAINT fk_project_canvases_project_id FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_project_canvases_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_project_canvases_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_project_order ON project_canvases (project_id, archived_at, position, id);

CREATE TABLE canvas_creation_resources (
	attempt_id BIGINT UNSIGNED NOT NULL, 
	user_id BIGINT UNSIGNED NOT NULL, 
	source_resource_id BIGINT UNSIGNED NOT NULL, 
	source_media_id BIGINT UNSIGNED, 
	source_binary_id BIGINT UNSIGNED, 
	snapshot_json JSON NOT NULL, 
	target_resource_id BIGINT UNSIGNED NOT NULL, 
	storage_locator VARCHAR(700) COLLATE utf8mb4_0900_bin NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	copied_at DATETIME(6), 
	released_at DATETIME(6), 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_creation_resource_owner FOREIGN KEY(attempt_id, user_id) REFERENCES canvas_creation_attempts (id, user_id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_creation_resource UNIQUE (attempt_id, source_resource_id), 
	CONSTRAINT uk_canvas_creation_target UNIQUE (target_resource_id), 
	CONSTRAINT uk_canvas_creation_locator UNIQUE (storage_locator), 
	CONSTRAINT ck_canvas_creation_source_pin CHECK ((released_at IS NULL AND ((source_media_id IS NULL) <> (source_binary_id IS NULL))) OR (released_at IS NOT NULL AND source_media_id IS NULL AND source_binary_id IS NULL)), 
	CONSTRAINT ck_canvas_creation_resource_state CHECK ((status = 'pending' AND copied_at IS NULL) OR (status IN ('copied','attached') AND copied_at IS NOT NULL AND released_at IS NOT NULL)), 
	CONSTRAINT ck_canvas_creation_resource_ids CHECK (source_resource_id > 0 AND target_resource_id > 0), 
	CONSTRAINT fk_canvas_creation_resources_source_media_id FOREIGN KEY(source_media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_creation_resources_source_binary_id FOREIGN KEY(source_binary_id) REFERENCES canvas_binary_resources (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_creation_resources_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_creation_resources_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_director_scenes (
	scene_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	position INTEGER UNSIGNED NOT NULL, 
	schema_version INTEGER UNSIGNED NOT NULL, 
	scene_json JSON NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_director_scenes_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_director_scene UNIQUE (canvas_id, scene_key), 
	CONSTRAINT ck_canvas_scene_version CHECK (row_version > 0 AND schema_version > 0), 
	CONSTRAINT fk_canvas_director_scenes_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_director_scenes_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

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

CREATE TABLE canvas_library_asset_references (
	id BIGINT UNSIGNED NOT NULL, 
	library_asset_id BIGINT UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED, 
	binary_id BIGINT UNSIGNED, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_library_media_ref UNIQUE (library_asset_id, media_id), 
	CONSTRAINT uk_canvas_library_binary_ref UNIQUE (library_asset_id, binary_id), 
	CONSTRAINT ck_canvas_library_resource_ref CHECK ((media_id IS NULL) <> (binary_id IS NULL)), 
	CONSTRAINT fk_canvas_library_asset_references_library_asset_id FOREIGN KEY(library_asset_id) REFERENCES canvas_library_assets (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_asset_references_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_asset_references_binary_id FOREIGN KEY(binary_id) REFERENCES canvas_binary_resources (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_library_folder_items (
	id BIGINT UNSIGNED NOT NULL, 
	library_asset_id BIGINT UNSIGNED NOT NULL, 
	folder_id BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_library_folder_item UNIQUE (library_asset_id), 
	CONSTRAINT fk_canvas_library_folder_items_library_asset_id FOREIGN KEY(library_asset_id) REFERENCES canvas_library_assets (id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_library_folder_items_folder_id FOREIGN KEY(folder_id) REFERENCES canvas_library_folders (id) ON DELETE CASCADE ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_library_folder_items ON canvas_library_folder_items (folder_id, library_asset_id);

CREATE TABLE canvas_nodes (
	node_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	kind VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	parent_node_key VARCHAR(128) COLLATE utf8mb4_0900_bin, 
	x DOUBLE NOT NULL, 
	y DOUBLE NOT NULL, 
	width DOUBLE NOT NULL, 
	height DOUBLE NOT NULL, 
	z_index INTEGER UNSIGNED NOT NULL, 
	content_json JSON NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	content_version BIGINT UNSIGNED NOT NULL, 
	archived_at DATETIME(6), 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_nodes_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_node_key UNIQUE (canvas_id, node_key), 
	CONSTRAINT fk_canvas_nodes_canvas_id FOREIGN KEY(canvas_id, parent_node_key) REFERENCES canvas_nodes (canvas_id, node_key) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT ck_canvas_node_dimensions CHECK (width > 0 AND height > 0), 
	CONSTRAINT ck_canvas_node_version CHECK (row_version > 0 AND content_version > 0), 
	CONSTRAINT fk_canvas_nodes_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_nodes_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_node_order ON canvas_nodes (canvas_id, archived_at, z_index);

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

CREATE TABLE canvas_resource_uploads (
	user_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED, 
	idempotency_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	declared_json JSON NOT NULL, 
	reserved_resource_id BIGINT UNSIGNED NOT NULL, 
	storage_locator VARCHAR(700) COLLATE utf8mb4_0900_bin NOT NULL, 
	byte_size BIGINT UNSIGNED NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	media_id BIGINT UNSIGNED, 
	binary_id BIGINT UNSIGNED, 
	scope_user_id BIGINT UNSIGNED, 
	project_id BIGINT UNSIGNED, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_upload_owning_canvas FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_upload_identity UNIQUE (user_id, idempotency_hash), 
	CONSTRAINT uk_canvas_upload_resource UNIQUE (reserved_resource_id), 
	CONSTRAINT ck_canvas_upload_scope CHECK ((scope_user_id IS NULL) <> (project_id IS NULL)), 
	CONSTRAINT ck_canvas_upload_canvas CHECK (canvas_id IS NULL OR project_id IS NOT NULL), 
	CONSTRAINT ck_canvas_upload_mode CHECK (mode IN ('multipart','chunked','copy')), 
	CONSTRAINT ck_canvas_upload_result CHECK ((status = 'pending' AND media_id IS NULL AND binary_id IS NULL) OR (status = 'ready' AND ((media_id IS NULL) <> (binary_id IS NULL)))), 
	CONSTRAINT ck_canvas_upload_size CHECK (byte_size > 0), 
	CONSTRAINT fk_canvas_resource_uploads_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_uploads_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_uploads_binary_id FOREIGN KEY(binary_id) REFERENCES canvas_binary_resources (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_uploads_scope_user_id FOREIGN KEY(scope_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_uploads_project_id FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_uploads_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_resource_uploads_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_upload_expiry ON canvas_resource_uploads (status, expires_at);

CREATE TABLE canvas_revisions (
	canvas_row_version BIGINT UNSIGNED NOT NULL, 
	schema_version INTEGER UNSIGNED NOT NULL, 
	snapshot_json JSON NOT NULL, 
	content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL, 
	reason VARCHAR(32) NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_revisions_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_revision_version UNIQUE (canvas_id, canvas_row_version), 
	CONSTRAINT uk_canvas_revision_id UNIQUE (canvas_id, id), 
	CONSTRAINT ck_canvas_revision_reason CHECK (reason IN ('automatic','before_restore')), 
	CONSTRAINT fk_canvas_revisions_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revisions_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_revision_time ON canvas_revisions (canvas_id, created_at, id);

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

CREATE TABLE canvas_timelines (
	schema_version INTEGER UNSIGNED NOT NULL, 
	document_json JSON NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_timelines_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_timeline UNIQUE (canvas_id), 
	CONSTRAINT ck_canvas_timeline_version CHECK (row_version > 0 AND schema_version > 0), 
	CONSTRAINT fk_canvas_timelines_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_timelines_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_user_states (
	user_id BIGINT UNSIGNED NOT NULL, 
	viewport_json JSON NOT NULL, 
	preferences_json JSON NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_user_states_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_user_state UNIQUE (user_id, canvas_id), 
	CONSTRAINT ck_canvas_user_state_version CHECK (row_version > 0), 
	CONSTRAINT fk_canvas_user_states_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_states_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_states_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_write_receipts (
	id BIGINT UNSIGNED NOT NULL, 
	actor_user_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED, 
	idempotency_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	operation_kind VARCHAR(64) NOT NULL, 
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL, 
	expected_row_version BIGINT UNSIGNED, 
	committed_row_version BIGINT UNSIGNED, 
	result_json JSON NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_write_receipts_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_write_receipt UNIQUE (actor_user_id, idempotency_key), 
	CONSTRAINT fk_canvas_write_receipts_actor_user_id FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_receipt_owning_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE project_canvas_settings (
	project_id BIGINT UNSIGNED NOT NULL, 
	primary_canvas_id BIGINT UNSIGNED, 
	workspace_key VARCHAR(64) COLLATE utf8mb4_0900_bin NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_settings_project UNIQUE (project_id), 
	CONSTRAINT uk_canvas_workspace_key UNIQUE (workspace_key), 
	CONSTRAINT fk_project_canvas_settings_project_id FOREIGN KEY(project_id, primary_canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT ck_canvas_settings_version CHECK (row_version > 0), 
	CONSTRAINT fk_canvas_settings_owning_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_project_canvas_settings_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_project_canvas_settings_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_binary_references (
	node_key VARCHAR(128) COLLATE utf8mb4_0900_bin, 
	revision_id BIGINT UNSIGNED, 
	owner_kind VARCHAR(32) NOT NULL, 
	owner_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	slot VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	ordinal INTEGER UNSIGNED NOT NULL, 
	binary_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_binary_references_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_binary_references_canvas_id FOREIGN KEY(canvas_id, revision_id) REFERENCES canvas_revisions (canvas_id, id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_binary_slot UNIQUE (canvas_id, owner_kind, owner_key, slot, ordinal), 
	CONSTRAINT fk_canvas_binary_owning_node FOREIGN KEY(canvas_id, node_key) REFERENCES canvas_nodes (canvas_id, node_key) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_binary_references_binary_id FOREIGN KEY(binary_id) REFERENCES canvas_binary_resources (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_binary_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_binary_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

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

CREATE TABLE canvas_edges (
	edge_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	from_node_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	to_node_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	from_port VARCHAR(128), 
	to_port VARCHAR(128), 
	relation VARCHAR(64), 
	position INTEGER UNSIGNED NOT NULL, 
	context_json JSON NOT NULL, 
	archived_at DATETIME(6), 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_edges_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_edge_key UNIQUE (canvas_id, edge_key), 
	CONSTRAINT fk_canvas_edge_from FOREIGN KEY(canvas_id, from_node_key) REFERENCES canvas_nodes (canvas_id, node_key) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_edge_to FOREIGN KEY(canvas_id, to_node_key) REFERENCES canvas_nodes (canvas_id, node_key) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT ck_canvas_edge_endpoints CHECK (from_node_key <> to_node_key), 
	CONSTRAINT fk_canvas_edges_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_edges_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_edge_order ON canvas_edges (canvas_id, archived_at, position);

CREATE TABLE canvas_media_references (
	owner_kind VARCHAR(32) NOT NULL, 
	owner_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	node_key VARCHAR(128) COLLATE utf8mb4_0900_bin, 
	slot VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	ordinal INTEGER UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_media_references_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_media_slot UNIQUE (canvas_id, owner_kind, owner_key, slot, ordinal), 
	CONSTRAINT fk_canvas_media_references_canvas_id FOREIGN KEY(canvas_id, node_key) REFERENCES canvas_nodes (canvas_id, node_key) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_media_references_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_media_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_media_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_node_user_states (
	user_id BIGINT UNSIGNED NOT NULL, 
	node_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	draft_json JSON NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_node_user_states_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_node_user_state UNIQUE (user_id, canvas_id, node_key), 
	CONSTRAINT fk_canvas_node_user_states_canvas_id FOREIGN KEY(canvas_id, node_key) REFERENCES canvas_nodes (canvas_id, node_key) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT ck_canvas_node_user_state_version CHECK (row_version > 0), 
	CONSTRAINT fk_canvas_node_user_states_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_node_user_states_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_node_user_states_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

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

CREATE TABLE canvas_resource_chunks (
	id BIGINT UNSIGNED NOT NULL, 
	upload_id BIGINT UNSIGNED NOT NULL, 
	chunk_index INTEGER UNSIGNED NOT NULL, 
	storage_locator VARCHAR(700) COLLATE utf8mb4_0900_bin NOT NULL, 
	byte_size INTEGER UNSIGNED NOT NULL, 
	checksum_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_upload_chunk UNIQUE (upload_id, chunk_index), 
	CONSTRAINT ck_canvas_chunk_size CHECK (byte_size > 0 AND byte_size <= 8388608), 
	CONSTRAINT fk_canvas_resource_chunks_upload_id FOREIGN KEY(upload_id) REFERENCES canvas_resource_uploads (id) ON DELETE CASCADE ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

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

CREATE TABLE canvas_revision_media_references (
	revision_id BIGINT UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_revision_media_references_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_revision_media UNIQUE (revision_id, media_id), 
	CONSTRAINT fk_canvas_revision_media_references_canvas_id FOREIGN KEY(canvas_id, revision_id) REFERENCES canvas_revisions (canvas_id, id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_media_references_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_media_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_media_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_revision_user_states (
	user_id BIGINT UNSIGNED NOT NULL, 
	revision_id BIGINT UNSIGNED NOT NULL, 
	state_json JSON NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_revision_user_states_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_revision_user_state UNIQUE (user_id, revision_id), 
	CONSTRAINT fk_canvas_revision_user_states_canvas_id FOREIGN KEY(canvas_id, revision_id) REFERENCES canvas_revisions (canvas_id, id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_user_states_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_user_states_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_revision_user_states_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

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

CREATE TABLE canvas_user_binary_references (
	user_id BIGINT UNSIGNED NOT NULL, 
	revision_id BIGINT UNSIGNED, 
	owner_kind VARCHAR(32) NOT NULL, 
	owner_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	slot VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	ordinal INTEGER UNSIGNED NOT NULL, 
	binary_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_user_binary_references_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_binary_references_canvas_id FOREIGN KEY(canvas_id, revision_id) REFERENCES canvas_revisions (canvas_id, id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_user_binary_slot UNIQUE (user_id, canvas_id, owner_kind, owner_key, slot, ordinal), 
	CONSTRAINT fk_canvas_user_binary_references_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_binary_references_binary_id FOREIGN KEY(binary_id) REFERENCES canvas_binary_resources (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_binary_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_binary_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_user_media_references (
	user_id BIGINT UNSIGNED NOT NULL, 
	revision_id BIGINT UNSIGNED, 
	owner_kind VARCHAR(32) NOT NULL, 
	owner_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	slot VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	ordinal INTEGER UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED NOT NULL, 
	project_id BIGINT UNSIGNED NOT NULL, 
	canvas_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_user_media_references_project_id FOREIGN KEY(project_id, canvas_id) REFERENCES project_canvases (project_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_media_references_canvas_id FOREIGN KEY(canvas_id, revision_id) REFERENCES canvas_revisions (canvas_id, id) ON DELETE CASCADE ON UPDATE RESTRICT, 
	CONSTRAINT uk_canvas_user_media_slot UNIQUE (user_id, canvas_id, owner_kind, owner_key, slot, ordinal), 
	CONSTRAINT fk_canvas_user_media_references_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_media_references_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_media_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_user_media_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
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
