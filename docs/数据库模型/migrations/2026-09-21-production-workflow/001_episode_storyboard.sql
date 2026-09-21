DELIMITER $$
DROP PROCEDURE IF EXISTS migrate_episode_storyboard$$
CREATE PROCEDURE migrate_episode_storyboard()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='episodes' AND column_name='storyboard_version') THEN
    ALTER TABLE episodes ADD COLUMN storyboard_version BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '分镜增改、关联、排序、归档与采用共用的乐观并发版本' AFTER content_version;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='episodes' AND constraint_name='ck_episodes_storyboard_version') THEN
    ALTER TABLE episodes ADD CONSTRAINT ck_episodes_storyboard_version CHECK (storyboard_version > 0);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='row_version') THEN
    ALTER TABLE shot_scripts ADD COLUMN row_version BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '单镜头保存、归档与采用的乐观并发版本' AFTER script;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='image_settings') THEN
    ALTER TABLE shot_scripts ADD COLUMN image_settings JSON NULL DEFAULT NULL COMMENT '下一次生图设置：resolution/aspect/layout；NULL按默认值读取' AFTER row_version;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='deleted_at') THEN
    ALTER TABLE shot_scripts ADD COLUMN deleted_at DATETIME(6) NULL DEFAULT NULL COMMENT '归档时间；非空行不参与活动分镜列表' AFTER image_settings;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='active_position') THEN
    ALTER TABLE shot_scripts ADD COLUMN active_position INT UNSIGNED GENERATED ALWAYS AS (CASE WHEN deleted_at IS NULL THEN position ELSE NULL END) STORED COMMENT '活动镜头顺序唯一键' AFTER deleted_at;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='creation_key') THEN
    ALTER TABLE shot_scripts ADD COLUMN creation_key VARCHAR(128) COLLATE utf8mb4_0900_bin NULL DEFAULT NULL COMMENT '手动新建幂等键' AFTER active_position;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='creation_hash') THEN
    ALTER TABLE shot_scripts ADD COLUMN creation_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL COMMENT '初始创建请求摘要' AFTER creation_key;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND index_name='uk_shots_episode_active_position') THEN
    ALTER TABLE shot_scripts ADD UNIQUE KEY uk_shots_episode_active_position (episode_id, active_position);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND index_name='uk_shots_creation_key') THEN
    ALTER TABLE shot_scripts ADD UNIQUE KEY uk_shots_creation_key (creation_key);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND index_name='idx_shots_episode_deleted_position') THEN
    ALTER TABLE shot_scripts ADD KEY idx_shots_episode_deleted_position (episode_id, deleted_at, position, id);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='shot_scripts' AND constraint_name='ck_shot_scripts_row_version') THEN
    ALTER TABLE shot_scripts ADD CONSTRAINT ck_shot_scripts_row_version CHECK (row_version > 0);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='shot_scripts' AND constraint_name='ck_shot_scripts_image_settings') THEN
    ALTER TABLE shot_scripts ADD CONSTRAINT ck_shot_scripts_image_settings CHECK (image_settings IS NULL OR JSON_TYPE(image_settings) = 'OBJECT');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='shot_scripts' AND constraint_name='ck_shot_scripts_deleted_time') THEN
    ALTER TABLE shot_scripts ADD CONSTRAINT ck_shot_scripts_deleted_time CHECK (deleted_at IS NULL OR created_at IS NULL OR deleted_at >= created_at);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='shot_scripts' AND constraint_name='ck_shot_scripts_creation') THEN
    ALTER TABLE shot_scripts ADD CONSTRAINT ck_shot_scripts_creation CHECK (
      (creation_key IS NULL AND creation_hash IS NULL)
      OR (creation_key IS NOT NULL AND CHAR_LENGTH(TRIM(creation_key)) > 0 AND creation_hash IS NOT NULL AND CHAR_LENGTH(creation_hash)=64)
    );
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND index_name='uk_shots_episode_position') THEN
    ALTER TABLE shot_scripts DROP INDEX uk_shots_episode_position;
  END IF;
END$$
CALL migrate_episode_storyboard()$$
DROP PROCEDURE migrate_episode_storyboard$$
DELIMITER ;
