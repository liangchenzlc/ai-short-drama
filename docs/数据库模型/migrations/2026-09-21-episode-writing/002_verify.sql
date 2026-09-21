-- Read-only postcheck. Compare field/check definitions with the README.
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'episodes'
  AND COLUMN_NAME IN ('editing_script_id', 'content_version');

SELECT tc.CONSTRAINT_NAME, tc.ENFORCED, cc.CHECK_CLAUSE
FROM information_schema.TABLE_CONSTRAINTS AS tc
JOIN information_schema.CHECK_CONSTRAINTS AS cc
  ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
 AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
WHERE tc.CONSTRAINT_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'episodes'
  AND tc.CONSTRAINT_NAME = 'ck_episodes_content_version';

-- All three counts must match the recorded precheck.
SELECT (SELECT COUNT(*) FROM episodes) AS episode_count,
       (SELECT COUNT(*) FROM episode_novels) AS novel_count,
       (SELECT COUNT(*) FROM episode_scripts) AS script_count;

-- All following result sets must be empty.
SELECT id, content_version FROM episodes WHERE content_version IS NULL OR content_version < 1;

SELECT e.id AS episode_id, e.editing_script_id
FROM episodes AS e LEFT JOIN episode_scripts AS s ON s.id = e.editing_script_id
WHERE e.editing_script_id IS NOT NULL AND (s.id IS NULL OR s.episode_id <> e.id);

SELECT e.id AS episode_id FROM episodes AS e
WHERE e.content_version = 1 AND e.editing_script_id IS NULL
  AND EXISTS (SELECT 1 FROM episode_scripts AS s WHERE s.episode_id = e.id);

SELECT episode_id, COUNT(*) AS confirmed_count
FROM episode_scripts WHERE state = 'confirmed'
GROUP BY episode_id HAVING COUNT(*) > 1;
