DELIMITER $$
DROP PROCEDURE IF EXISTS verify_production_workflow$$
CREATE PROCEDURE verify_production_workflow()
BEGIN
  DECLARE v_episodes BIGINT UNSIGNED;
  DECLARE v_shot_scripts BIGINT UNSIGNED;
  DECLARE v_shot_assets BIGINT UNSIGNED;
  DECLARE v_shot_images BIGINT UNSIGNED;
  DECLARE v_shot_videos BIGINT UNSIGNED;
  DECLARE v_script_shot_records BIGINT UNSIGNED;
  DECLARE v_media_recycle_bin BIGINT UNSIGNED;
  DECLARE v_assets BIGINT UNSIGNED;

  IF EXISTS (
    SELECT required.name FROM (
      SELECT 'episodes.storyboard_version' name UNION ALL
      SELECT 'shot_scripts.row_version' UNION ALL SELECT 'shot_scripts.image_settings'
      UNION ALL SELECT 'shot_scripts.deleted_at' UNION ALL SELECT 'shot_scripts.active_position'
      UNION ALL SELECT 'shot_scripts.creation_key' UNION ALL SELECT 'shot_scripts.creation_hash'
      UNION ALL SELECT 'assets.row_version' UNION ALL SELECT 'assets.state'
      UNION ALL SELECT 'assets.tags' UNION ALL SELECT 'assets.scene_time'
      UNION ALL SELECT 'assets.creation_key' UNION ALL SELECT 'assets.creation_hash'
      UNION ALL SELECT 'shot_images.context_hash'
    ) required
    LEFT JOIN information_schema.columns c
      ON c.table_schema=DATABASE()
     AND CONCAT(c.table_name,'.',c.column_name)=required.name
    WHERE c.column_name IS NULL
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: required column missing';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='asset_image_candidates') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: candidate table missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema=DATABASE() AND table_name='episodes' AND column_name='storyboard_version'
      AND column_type='bigint unsigned' AND is_nullable='NO' AND column_default='1'
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: storyboard_version definition mismatch';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND (
      (column_name='row_version' AND (column_type<>'bigint unsigned' OR is_nullable<>'NO' OR column_default<>'1'))
      OR (column_name='image_settings' AND (data_type<>'json' OR is_nullable<>'YES'))
      OR (column_name='deleted_at' AND (data_type<>'datetime' OR datetime_precision<>6 OR is_nullable<>'YES'))
      OR (column_name='creation_key' AND (column_type<>'varchar(128)' OR collation_name<>'utf8mb4_0900_bin'))
      OR (column_name='creation_hash' AND (column_type<>'char(64)' OR character_set_name<>'ascii' OR collation_name<>'ascii_bin'))
    )
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: shot column definition mismatch';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='active_position'
      AND column_type='int unsigned' AND LOCATE('STORED GENERATED', extra) > 0
      AND generation_expression<>''
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: active_position definition mismatch';
  END IF;
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema=DATABASE() AND table_name='assets' AND (
      (column_name='row_version' AND (column_type<>'bigint unsigned' OR is_nullable<>'NO' OR column_default<>'1'))
      OR (column_name='state' AND (column_type<>'varchar(16)' OR is_nullable<>'NO' OR column_default<>'unconfirmed' OR collation_name<>'utf8mb4_0900_bin'))
      OR (column_name='tags' AND (data_type<>'json' OR is_nullable<>'NO'))
      OR (column_name='scene_time' AND (column_type<>'varchar(60)' OR is_nullable<>'NO'))
      OR (column_name='creation_key' AND (column_type<>'varchar(128)' OR collation_name<>'utf8mb4_0900_bin'))
      OR (column_name='creation_hash' AND (column_type<>'char(64)' OR character_set_name<>'ascii' OR collation_name<>'ascii_bin'))
    )
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: asset column definition mismatch';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema=DATABASE() AND table_name='shot_images' AND column_name='context_hash'
      AND column_type='char(64)' AND character_set_name='ascii' AND collation_name='ascii_bin' AND is_nullable='YES'
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: context_hash definition mismatch';
  END IF;
  IF (
    SELECT COUNT(*) FROM information_schema.table_constraints
    WHERE constraint_schema=DATABASE() AND enforced='YES'
      AND constraint_name IN (
        'ck_episodes_storyboard_version','ck_shot_scripts_row_version',
        'ck_shot_scripts_image_settings','ck_shot_scripts_deleted_time',
        'ck_shot_scripts_creation','ck_assets_row_version','ck_assets_state',
        'ck_assets_confirmed_media','ck_assets_tags','ck_assets_scene_time',
        'ck_assets_creation_pair','ck_shot_images_context_hash'
      )
  ) <> 12 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: required enforced CHECK missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND index_name='uk_shots_episode_active_position' AND non_unique=0) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: active position unique index missing';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND index_name='uk_shots_episode_position') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: obsolete position index remains';
  END IF;
  IF EXISTS (SELECT 1 FROM episodes WHERE storyboard_version=0 LIMIT 1)
     OR EXISTS (SELECT 1 FROM shot_scripts WHERE row_version=0 LIMIT 1)
     OR EXISTS (SELECT 1 FROM assets WHERE row_version=0 LIMIT 1) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: zero version found';
  END IF;
  IF EXISTS (
    SELECT 1 FROM assets a LEFT JOIN asset_image_candidates c
      ON c.asset_id=a.id AND c.media_id=a.media_id
    WHERE a.media_id IS NOT NULL AND c.id IS NULL LIMIT 1
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: candidate backfill incomplete';
  END IF;
  IF EXISTS (
    SELECT 1 FROM asset_image_candidates c
    LEFT JOIN assets a ON a.id=c.asset_id LEFT JOIN media_files m ON m.id=c.media_id
    WHERE a.id IS NULL OR m.id IS NULL LIMIT 1
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: candidate relation invalid';
  END IF;

  SELECT
    MAX(CASE WHEN table_name='episodes' THEN row_count END),
    MAX(CASE WHEN table_name='shot_scripts' THEN row_count END),
    MAX(CASE WHEN table_name='shot_assets' THEN row_count END),
    MAX(CASE WHEN table_name='shot_images' THEN row_count END),
    MAX(CASE WHEN table_name='shot_videos' THEN row_count END),
    MAX(CASE WHEN table_name='script_shot_records' THEN row_count END),
    MAX(CASE WHEN table_name='media_recycle_bin' THEN row_count END),
    MAX(CASE WHEN table_name='assets' THEN row_count END)
  INTO
    v_episodes,
    v_shot_scripts,
    v_shot_assets,
    v_shot_images,
    v_shot_videos,
    v_script_shot_records,
    v_media_recycle_bin,
    v_assets
  FROM production_workflow_baseline;

  IF v_episodes<>(SELECT COUNT(*) FROM episodes)
     OR v_shot_scripts<>(SELECT COUNT(*) FROM shot_scripts)
     OR v_shot_assets<>(SELECT COUNT(*) FROM shot_assets)
     OR v_shot_images<>(SELECT COUNT(*) FROM shot_images)
     OR v_shot_videos<>(SELECT COUNT(*) FROM shot_videos)
     OR v_script_shot_records<>(SELECT COUNT(*) FROM script_shot_records)
     OR v_media_recycle_bin<>(SELECT COUNT(*) FROM media_recycle_bin)
     OR v_assets<>(SELECT COUNT(*) FROM assets) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='production workflow migration: protected row count changed';
  END IF;
END$$
CALL verify_production_workflow()$$
DROP PROCEDURE verify_production_workflow$$
DELIMITER ;

SELECT 'production workflow migration verified' AS result;
