DELIMITER $$
DROP PROCEDURE IF EXISTS verify_storyboard_prompt_fields$$
CREATE PROCEDURE verify_storyboard_prompt_fields()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shot_scripts'
      AND column_name = 'duration_ms' AND column_type = 'int unsigned'
      AND is_nullable = 'NO' AND column_default = '3000'
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'storyboard prompt migration: duration_ms definition mismatch';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'shot_scripts'
      AND column_name = 'source_excerpt' AND data_type = 'mediumtext'
      AND is_nullable = 'NO'
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'storyboard prompt migration: source_excerpt definition mismatch';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_schema = DATABASE() AND table_name = 'shot_scripts'
      AND constraint_name = 'ck_shot_scripts_duration_ms' AND enforced = 'YES'
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'storyboard prompt migration: duration check missing';
  END IF;

  IF EXISTS (
    SELECT 1 FROM shot_scripts
    WHERE duration_ms < 1000 OR duration_ms > 10000 OR source_excerpt IS NULL
    LIMIT 1
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'storyboard prompt migration: invalid shot data';
  END IF;

  IF (SELECT row_count FROM storyboard_prompt_baseline WHERE table_name = 'shot_scripts')
     <> (SELECT COUNT(*) FROM shot_scripts) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'storyboard prompt migration: protected row count changed';
  END IF;
END$$
CALL verify_storyboard_prompt_fields()$$
DROP PROCEDURE verify_storyboard_prompt_fields$$
DELIMITER ;

SELECT 'storyboard prompt migration verified' AS result;
