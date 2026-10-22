-- 画布完整风格、本人偏好与兼容播放；执行前备份并检查现有结构。

CREATE TABLE canvas_style_profiles (
	profile_json JSON NOT NULL, 
	row_version BIGINT UNSIGNED NOT NULL, 
	import_key VARCHAR(128) COLLATE utf8mb4_0900_bin, 
	favorite BIGINT UNSIGNED NOT NULL, 
	last_used_at DATETIME(6), 
	deleted_at DATETIME(6), 
	scope_user_id BIGINT UNSIGNED, 
	project_id BIGINT UNSIGNED, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_source_style UNIQUE (project_id), 
	CONSTRAINT uk_canvas_style_import UNIQUE (scope_user_id, import_key), 
	CONSTRAINT ck_canvas_style_scope CHECK ((scope_user_id IS NULL) <> (project_id IS NULL)), 
	CONSTRAINT ck_canvas_style_version CHECK (row_version > 0 AND favorite IN (0,1)), 
	CONSTRAINT ck_canvas_style_profile CHECK (JSON_TYPE(profile_json) = 'OBJECT'), 
	CONSTRAINT fk_canvas_style_profiles_scope_user_id FOREIGN KEY(scope_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_profiles_project_id FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_profiles_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_profiles_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_personal_styles ON canvas_style_profiles (scope_user_id, deleted_at, updated_at);

CREATE TABLE canvas_style_media_references (
	style_id BIGINT UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED NOT NULL, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_style_media UNIQUE (style_id, media_id), 
	CONSTRAINT fk_canvas_style_media_references_style_id FOREIGN KEY(style_id) REFERENCES canvas_style_profiles (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_media_references_media_id FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_media_references_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_media_references_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_style_preferences (
	user_id BIGINT UNSIGNED NOT NULL, 
	preset_id VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	favorite BIGINT UNSIGNED NOT NULL, 
	last_used_at DATETIME(6), 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_style_preference UNIQUE (user_id, preset_id), 
	CONSTRAINT ck_canvas_style_preference CHECK (favorite IN (0,1)), 
	CONSTRAINT fk_canvas_style_preferences_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_preferences_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_style_preferences_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

ALTER TABLE canvas_local_jobs DROP CHECK ck_canvas_local_type, ADD CONSTRAINT ck_canvas_local_type CHECK (task_type IN ('timeline_transcription','depth_capture','resource_playback'));

ALTER TABLE canvas_local_jobs DROP CHECK ck_canvas_local_output, ADD CONSTRAINT ck_canvas_local_output CHECK (status <> 'succeeded' OR (result_json IS NOT NULL AND finished_at IS NOT NULL AND (task_type NOT IN ('depth_capture','resource_playback') OR output_media_id IS NOT NULL OR disposal_receipt_id IS NOT NULL)));
