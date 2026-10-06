-- 本人模型渠道目录及稳定执行配置绑定；用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_model_catalogs (
	user_id BIGINT UNSIGNED NOT NULL, 
	channels_json JSON NOT NULL, 
	credentials_cipher MEDIUMTEXT, 
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_model_catalog_user UNIQUE (user_id), 
	CONSTRAINT fk_canvas_model_catalogs_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_model_catalogs_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_model_catalogs_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE canvas_channel_models (
	user_id BIGINT UNSIGNED NOT NULL, 
	channel_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL, 
	model_key VARCHAR(255) COLLATE utf8mb4_0900_bin NOT NULL, 
	model_config_id BIGINT UNSIGNED NOT NULL, 
	runtime_migrated_at DATETIME(3),
	id BIGINT UNSIGNED NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BIGINT UNSIGNED NOT NULL, 
	updated_by BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_canvas_channel_model UNIQUE (user_id, channel_key, model_key), 
	CONSTRAINT uk_canvas_channel_config UNIQUE (model_config_id), 
	CONSTRAINT fk_canvas_channel_models_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_channel_models_model_config_id FOREIGN KEY(model_config_id) REFERENCES ai_model_configs (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_channel_models_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_canvas_channel_models_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
