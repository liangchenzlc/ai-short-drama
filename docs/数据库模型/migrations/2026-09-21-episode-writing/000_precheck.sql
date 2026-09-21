-- Read-only. Select the intended database before execution. MySQL 8.0.21+.
SELECT DATABASE() AS selected_database, VERSION() AS mysql_version,
       @@SESSION.sql_mode AS sql_mode, @@SESSION.time_zone AS time_zone;

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

-- Record these counts before migration and compare afterwards.
SELECT (SELECT COUNT(*) FROM episodes) AS episode_count,
       (SELECT COUNT(*) FROM episode_novels) AS novel_count,
       (SELECT COUNT(*) FROM episode_scripts) AS script_count;

-- Each following result must be empty. Resolve unexpected legacy data first.
SELECT episode_id, COUNT(*) AS confirmed_count
FROM episode_scripts WHERE state = 'confirmed'
GROUP BY episode_id HAVING COUNT(*) > 1;

SELECT s.id AS orphan_script_id FROM episode_scripts AS s
LEFT JOIN episodes AS e ON e.id = s.episode_id WHERE e.id IS NULL;

-- Preview the deterministic selection without modifying existing scripts.
SELECT e.id AS episode_id,
       (SELECT s.id FROM episode_scripts AS s WHERE s.episode_id = e.id
        ORDER BY (s.state = 'confirmed') DESC, s.position ASC, s.id ASC LIMIT 1)
         AS initial_editing_script_id
FROM episodes AS e ORDER BY e.id;
