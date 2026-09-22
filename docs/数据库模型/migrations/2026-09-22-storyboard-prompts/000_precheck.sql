-- Run 000..002 in one MySQL 8 client session so the temporary baseline survives.
SELECT VERSION() AS mysql_version,
       (CAST(SUBSTRING_INDEX(VERSION(), '.', 1) AS UNSIGNED) >= 8) AS version_ok;

SELECT COUNT(*) = 1 AS shot_scripts_exists
FROM information_schema.tables
WHERE table_schema = DATABASE() AND table_name = 'shot_scripts';

DROP TEMPORARY TABLE IF EXISTS storyboard_prompt_baseline;
CREATE TEMPORARY TABLE storyboard_prompt_baseline (
  table_name VARCHAR(64) PRIMARY KEY,
  row_count BIGINT UNSIGNED NOT NULL
);
INSERT INTO storyboard_prompt_baseline VALUES
  ('shot_scripts', (SELECT COUNT(*) FROM shot_scripts));

SELECT * FROM storyboard_prompt_baseline;
