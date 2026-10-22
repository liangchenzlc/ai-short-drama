-- 本人 BeefAPI 设备授权与加密凭据；用 canvas_migration.py --apply 安全重入。
CREATE TABLE canvas_beefapi_connections (
	user_id BIGINT UNSIGNED NOT NULL,
	state_json JSON NOT NULL,
	secrets_cipher MEDIUMTEXT,
	row_version BIGINT UNSIGNED NOT NULL,
	next_poll_at DATETIME(6),
	lease_until DATETIME(6),
	lease_owner VARCHAR(64),
	id BIGINT UNSIGNED NOT NULL,
	created_at DATETIME(6) NOT NULL,
	updated_at DATETIME(6) NOT NULL,
	created_by BIGINT UNSIGNED NOT NULL,
	updated_by BIGINT UNSIGNED NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uk_canvas_beefapi_user UNIQUE (user_id),
	CONSTRAINT ck_canvas_beefapi_version CHECK (row_version > 0),
	CONSTRAINT fk_canvas_beefapi_connections_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_canvas_beefapi_connections_created_by FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_canvas_beefapi_connections_updated_by FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_canvas_beefapi_poll ON canvas_beefapi_connections (next_poll_at, lease_until, id);
