-- 角色不可变版本结构；不回填角色内容/项目来源，不构造历史或声音绑定。
-- 仅全缺结构可直接执行；部分完成或重复执行用 asset_version_migration.py --apply。
CREATE TABLE asset_versions (
	id BIGINT UNSIGNED NOT NULL, 
	asset_id BIGINT UNSIGNED NOT NULL, 
	version BIGINT UNSIGNED NOT NULL, 
	source_row_version BIGINT UNSIGNED NOT NULL, 
	snapshot_json JSON NOT NULL, 
	content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, 
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), 
	created_by BIGINT UNSIGNED DEFAULT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_asset_versions_asset FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_asset_versions_owner_id UNIQUE (asset_id, id), 
	CONSTRAINT uk_asset_versions_version UNIQUE (asset_id, version), 
	CONSTRAINT ck_asset_versions_version CHECK (version > 0), 
	CONSTRAINT ck_asset_versions_source_version CHECK (source_row_version > 0), 
	CONSTRAINT ck_asset_versions_snapshot CHECK (JSON_TYPE(snapshot_json) = 'OBJECT'), 
	CONSTRAINT ck_asset_versions_hash CHECK (CHAR_LENGTH(content_hash) = 64)
)ENGINE=InnoDB ROW_FORMAT=DYNAMIC CHARSET=utf8mb4 COMMENT='角色不可变作品版本；审计不编造旧历史' COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE asset_version_media_references (
	id BIGINT UNSIGNED NOT NULL, 
	asset_id BIGINT UNSIGNED NOT NULL, 
	version_id BIGINT UNSIGNED NOT NULL, 
	purpose VARCHAR(24) COLLATE utf8mb4_0900_bin NOT NULL, 
	`role` VARCHAR(32) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT '', 
	ordinal INTEGER UNSIGNED NOT NULL, 
	media_id BIGINT UNSIGNED NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_asset_version_media_version FOREIGN KEY(asset_id, version_id) REFERENCES asset_versions (asset_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_asset_version_media_file FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT uk_asset_version_media_slot UNIQUE (asset_id, version_id, purpose, ordinal), 
	CONSTRAINT ck_asset_version_media_ordinal CHECK (ordinal >= 0), 
	CONSTRAINT ck_asset_version_media_purpose_role CHECK ((purpose = 'representation' AND `role` IN ('primary','front','side','back','turnaround_sheet','expression_sheet')) OR (purpose = 'generation_reference' AND `role` = ''))
)ENGINE=InnoDB ROW_FORMAT=DYNAMIC CHARSET=utf8mb4 COMMENT='角色版本表示图及生成参考；真实媒体外键保护历史' COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_asset_version_media_file ON asset_version_media_references (media_id);

ALTER TABLE assets ADD COLUMN current_version_id BIGINT UNSIGNED COMMENT '角色当前不可变作品版本；非角色及未回填旧数据为空' DEFAULT NULL;

CREATE INDEX idx_assets_current_version ON assets (id, current_version_id);

ALTER TABLE assets ADD CONSTRAINT ck_assets_character_version CHECK (kind = 'character' OR current_version_id IS NULL);

ALTER TABLE assets ADD CONSTRAINT fk_assets_current_version FOREIGN KEY(id, current_version_id) REFERENCES asset_versions (asset_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT;
