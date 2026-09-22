DELIMITER $$
DROP PROCEDURE IF EXISTS migrate_storyboard_prompt_fields$$
CREATE PROCEDURE migrate_storyboard_prompt_fields()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shot_scripts'
      AND column_name = 'duration_ms'
  ) THEN
    ALTER TABLE shot_scripts
      ADD COLUMN duration_ms INT UNSIGNED NOT NULL DEFAULT 3000
        COMMENT '建议镜头时长（毫秒）' AFTER script;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shot_scripts'
      AND column_name = 'source_excerpt'
  ) THEN
    ALTER TABLE shot_scripts
      ADD COLUMN source_excerpt MEDIUMTEXT NOT NULL DEFAULT ('')
        COMMENT '生成分镜的连续剧本原文依据' AFTER duration_ms;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_schema = DATABASE() AND table_name = 'shot_scripts'
      AND constraint_name = 'ck_shot_scripts_duration_ms'
  ) THEN
    ALTER TABLE shot_scripts
      ADD CONSTRAINT ck_shot_scripts_duration_ms
      CHECK (duration_ms BETWEEN 1000 AND 10000);
  END IF;
END$$
CALL migrate_storyboard_prompt_fields()$$
DROP PROCEDURE migrate_storyboard_prompt_fields$$
DELIMITER ;
