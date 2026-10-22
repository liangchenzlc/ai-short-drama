-- Existing databases only. Apply once after backing up the selected application database.
ALTER TABLE assets ADD COLUMN reference_media_ids JSON NOT NULL DEFAULT (JSON_ARRAY()) COMMENT '持久化生成参考图片' AFTER media_id;
ALTER TABLE shot_scripts ADD COLUMN reference_media_ids JSON NOT NULL DEFAULT (JSON_ARRAY()) COMMENT '持久化生成参考图片' AFTER source_excerpt;
