-- Run in one mysql client session together with 001..005 so the temporary baseline survives.
-- Stop if version_ok=0 or any *_problems value is nonzero.
SELECT VERSION() AS mysql_version,
       (CAST(SUBSTRING_INDEX(VERSION(), '.', 1) AS UNSIGNED) > 8
        OR (CAST(SUBSTRING_INDEX(VERSION(), '.', 1) AS UNSIGNED) = 8
            AND (CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(VERSION(), '.', 2), '.', -1) AS UNSIGNED) > 0
                 OR (CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(VERSION(), '.', 2), '.', -1) AS UNSIGNED) = 0
                     AND CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(VERSION(), '-', 1), '.', -1) AS UNSIGNED) >= 16)))
       ) AS version_ok;

DROP TEMPORARY TABLE IF EXISTS production_workflow_baseline;
CREATE TEMPORARY TABLE production_workflow_baseline (
  table_name VARCHAR(64) PRIMARY KEY,
  row_count BIGINT UNSIGNED NOT NULL
);
INSERT INTO production_workflow_baseline VALUES
  ('episodes', (SELECT COUNT(*) FROM episodes)),
  ('shot_scripts', (SELECT COUNT(*) FROM shot_scripts)),
  ('shot_assets', (SELECT COUNT(*) FROM shot_assets)),
  ('shot_images', (SELECT COUNT(*) FROM shot_images)),
  ('shot_videos', (SELECT COUNT(*) FROM shot_videos)),
  ('script_shot_records', (SELECT COUNT(*) FROM script_shot_records)),
  ('media_recycle_bin', (SELECT COUNT(*) FROM media_recycle_bin)),
  ('assets', (SELECT COUNT(*) FROM assets));

SET @shot_position_sql = IF(
  EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_scripts' AND column_name='deleted_at'),
  'SELECT COUNT(*) AS duplicate_active_shot_positions FROM (SELECT episode_id,position FROM shot_scripts WHERE deleted_at IS NULL GROUP BY episode_id,position HAVING COUNT(*)>1) d',
  'SELECT COUNT(*) AS duplicate_active_shot_positions FROM (SELECT episode_id,position FROM shot_scripts GROUP BY episode_id,position HAVING COUNT(*)>1) d'
);
PREPARE shot_position_check FROM @shot_position_sql;
EXECUTE shot_position_check;
DEALLOCATE PREPARE shot_position_check;

SELECT COUNT(*) AS invalid_asset_media_references
FROM assets AS a LEFT JOIN media_files AS m ON m.id = a.media_id
WHERE a.media_id IS NOT NULL AND m.id IS NULL;

SELECT
  (SELECT COUNT(*) FROM shot_assets sa LEFT JOIN shot_scripts s ON s.id=sa.shot_id AND s.episode_id=sa.episode_id WHERE s.id IS NULL)
    AS invalid_shot_asset_references,
  (SELECT COUNT(*) FROM shot_images si LEFT JOIN shot_scripts s ON s.id=si.shot_id AND s.episode_id=si.episode_id WHERE s.id IS NULL)
    AS invalid_shot_image_references,
  (SELECT COUNT(*) FROM shot_videos sv LEFT JOIN shot_scripts s ON s.id=sv.shot_id AND s.episode_id=sv.episode_id WHERE s.id IS NULL)
    AS invalid_shot_video_references;

SELECT * FROM production_workflow_baseline ORDER BY table_name;
