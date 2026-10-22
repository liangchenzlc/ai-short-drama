DELIMITER $$
DROP PROCEDURE IF EXISTS migrate_asset_persistence$$
CREATE PROCEDURE migrate_asset_persistence()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='assets' AND column_name='row_version') THEN
    ALTER TABLE assets ADD COLUMN row_version BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '素材元信息与采用图片的乐观并发版本' AFTER media_id;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='assets' AND column_name='state') THEN
    ALTER TABLE assets ADD COLUMN state VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'unconfirmed' COMMENT '素材确认状态' AFTER row_version;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='assets' AND column_name='tags') THEN
    ALTER TABLE assets ADD COLUMN tags JSON NOT NULL DEFAULT (JSON_ARRAY()) COMMENT '去空去重后的标签数组，最多20项' AFTER state;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='assets' AND column_name='scene_time') THEN
    ALTER TABLE assets ADD COLUMN scene_time VARCHAR(60) NOT NULL DEFAULT '' COMMENT '场景时间；非场景必须为空' AFTER tags;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='assets' AND column_name='creation_key') THEN
    ALTER TABLE assets ADD COLUMN creation_key VARCHAR(128) COLLATE utf8mb4_0900_bin NULL DEFAULT NULL COMMENT '手动新建幂等键' AFTER scene_time;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='assets' AND column_name='creation_hash') THEN
    ALTER TABLE assets ADD COLUMN creation_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL COMMENT '初始创建请求摘要' AFTER creation_key;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='assets' AND index_name='uk_assets_creation_key') THEN
    ALTER TABLE assets ADD UNIQUE KEY uk_assets_creation_key (creation_key);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='assets' AND constraint_name='ck_assets_row_version') THEN
    ALTER TABLE assets ADD CONSTRAINT ck_assets_row_version CHECK (row_version > 0);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='assets' AND constraint_name='ck_assets_state') THEN
    ALTER TABLE assets ADD CONSTRAINT ck_assets_state CHECK (state IN ('unconfirmed','confirmed'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='assets' AND constraint_name='ck_assets_confirmed_media') THEN
    ALTER TABLE assets ADD CONSTRAINT ck_assets_confirmed_media CHECK (state <> 'confirmed' OR media_id IS NOT NULL);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='assets' AND constraint_name='ck_assets_tags') THEN
    ALTER TABLE assets ADD CONSTRAINT ck_assets_tags CHECK (JSON_TYPE(tags)='ARRAY' AND JSON_LENGTH(tags)<=20);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='assets' AND constraint_name='ck_assets_scene_time') THEN
    ALTER TABLE assets ADD CONSTRAINT ck_assets_scene_time CHECK (kind='scene' OR scene_time='');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='assets' AND constraint_name='ck_assets_creation_pair') THEN
    ALTER TABLE assets ADD CONSTRAINT ck_assets_creation_pair CHECK (
      (creation_key IS NULL AND creation_hash IS NULL)
      OR (creation_key IS NOT NULL AND CHAR_LENGTH(TRIM(creation_key))>0 AND creation_hash IS NOT NULL AND CHAR_LENGTH(creation_hash)=64)
    );
  END IF;
END$$
CALL migrate_asset_persistence()$$
DROP PROCEDURE migrate_asset_persistence$$
DELIMITER ;

CREATE TABLE IF NOT EXISTS asset_image_candidates (
  id BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  asset_id BIGINT UNSIGNED NOT NULL COMMENT '角色、场景或道具本体',
  media_id BIGINT UNSIGNED NOT NULL COMMENT '永久媒体文件',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uk_asset_image_candidates_media (asset_id, media_id),
  KEY idx_asset_image_candidates_time (asset_id, created_at, id),
  KEY idx_asset_image_candidates_media (media_id),
  CONSTRAINT fk_asset_image_candidates_asset FOREIGN KEY (asset_id)
    REFERENCES assets(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_asset_image_candidates_media FOREIGN KEY (media_id)
    REFERENCES media_files(id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_0900_ai_ci COMMENT='素材参考图片候选';
