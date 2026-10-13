-- 标准模式创作 C3-C5 增量：仅执行于缺失这些列和表的旧库。
-- 已存在部分结构时请使用 backend/scripts/standard_creation_ddl.py --apply。

ALTER TABLE `shot_scripts` ADD COLUMN `title` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '镜头名称；旧记录为空' AFTER `row_version`, ADD COLUMN `shot_size` VARCHAR(20) NULL DEFAULT NULL COMMENT '受限景别元信息' AFTER `title`, ADD CONSTRAINT `ck_shot_scripts_shot_size` CHECK (`shot_size` IS NULL OR `shot_size` IN ('full_shot','medium_shot','close_up','extreme_close_up','wide_shot','two_shot'));

ALTER TABLE `episode_scripts` ADD COLUMN `editor_document` JSON NULL COMMENT '与纯文本同版本保存的受限 Quill Delta' AFTER `content`, ADD CONSTRAINT `ck_scripts_editor_document` CHECK (`editor_document` IS NULL OR JSON_TYPE(`editor_document`) = 'OBJECT');

CREATE TABLE user_creation_drafts (
	id BIGINT UNSIGNED NOT NULL COMMENT 'Snowflake-generated stable identifier', 
	user_id BIGINT UNSIGNED NOT NULL COMMENT '本人草稿所有者', 
	project_id BIGINT UNSIGNED NOT NULL COMMENT 'Project scope', 
	episode_id BIGINT UNSIGNED NOT NULL COMMENT 'Episode scope', 
	object_id BIGINT UNSIGNED NOT NULL COMMENT '分集、素材或镜头的稳定对象ID', 
	purpose VARCHAR(32) COLLATE utf8mb4_0900_bin NOT NULL COMMENT 'novel_adaptation / asset_image / storyboard_image / storyboard_video', 
	model_config_id BIGINT UNSIGNED COMMENT 'Saved model choice ID', 
	input_text MEDIUMTEXT NOT NULL COMMENT '本人本次输入；图片和改编最多4000字符，视频最多16000字符' DEFAULT (''), 
	image_settings JSON COMMENT 'Image generation settings: aspect, resolution, count' DEFAULT NULL, 
	video_settings JSON COMMENT 'Video generation settings: resolution, duration_ms' DEFAULT NULL, 
	draft_version BIGINT UNSIGNED NOT NULL COMMENT 'Local draft version for UI conflict detection' DEFAULT 1, 
	reference_media_ids JSON NOT NULL COMMENT '个人参考图永久媒体ID；不保存展示URL' DEFAULT (JSON_ARRAY()), 
	source_version BIGINT UNSIGNED COMMENT 'Source object version when draft was last saved' DEFAULT NULL, 
	row_version BIGINT UNSIGNED NOT NULL COMMENT 'Optimistic concurrency token for server updates' DEFAULT 1, 
	created_at DATETIME(6) COMMENT 'Creation timestamp' DEFAULT CURRENT_TIMESTAMP(6), 
	updated_at DATETIME(6) COMMENT 'Last modification timestamp' DEFAULT CURRENT_TIMESTAMP(6), 
	PRIMARY KEY (id), 
	CONSTRAINT uk_user_creation_drafts_scope UNIQUE (user_id, project_id, episode_id, object_id, purpose), 
	CONSTRAINT fk_user_creation_drafts_user_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_user_creation_drafts_project_id FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT fk_user_creation_drafts_episode_id FOREIGN KEY(episode_id) REFERENCES episodes (id) ON DELETE RESTRICT ON UPDATE RESTRICT, 
	CONSTRAINT ck_user_creation_drafts_row_version CHECK (`row_version` > 0), 
	CONSTRAINT ck_user_creation_drafts_draft_version CHECK (`draft_version` > 0), 
	CONSTRAINT ck_user_creation_drafts_purpose CHECK (`purpose` IN ('novel_adaptation', 'asset_image', 'storyboard_image', 'storyboard_video')), 
	CONSTRAINT ck_user_creation_drafts_image_settings CHECK (`image_settings` IS NULL OR JSON_TYPE(`image_settings`) = 'OBJECT'), 
	CONSTRAINT ck_user_creation_drafts_video_settings CHECK (`video_settings` IS NULL OR JSON_TYPE(`video_settings`) = 'OBJECT'), 
	CONSTRAINT ck_user_creation_drafts_reference_media_ids CHECK (JSON_TYPE(`reference_media_ids`) = 'ARRAY' AND JSON_LENGTH(`reference_media_ids`) <= 16)
)ENGINE=InnoDB ROW_FORMAT=DYNAMIC CHARSET=utf8mb4 COMMENT='本人、项目、分集、对象和用途独立的生成草稿' COLLATE utf8mb4_0900_ai_ci;
CREATE INDEX idx_user_creation_drafts_user_episode ON user_creation_drafts (user_id, episode_id);
CREATE INDEX idx_user_creation_drafts_user_project ON user_creation_drafts (user_id, project_id);
