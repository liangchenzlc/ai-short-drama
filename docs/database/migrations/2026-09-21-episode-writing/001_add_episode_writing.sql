-- Select the intended database, pause every writer, and run 000_precheck.sql first.
-- Additive migration. MySQL DDL implicitly commits. Keep one connection throughout.
-- Existing columns/checks are skipped, never changed. Verify their exact definitions.
SET @writing_pointer_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'episodes'
    AND COLUMN_NAME = 'editing_script_id'
);
SET @writing_ddl = IF(@writing_pointer_exists = 0,
  'ALTER TABLE episodes ADD COLUMN editing_script_id BIGINT UNSIGNED NULL DEFAULT NULL COMMENT ''Current editing script, same episode enforced by application''',
  'SELECT ''editing_script_id already exists'' AS migration_status');
PREPARE writing_migration FROM @writing_ddl;
EXECUTE writing_migration;
DEALLOCATE PREPARE writing_migration;

SET @writing_version_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'episodes'
    AND COLUMN_NAME = 'content_version'
);
SET @writing_ddl = IF(@writing_version_exists = 0,
  'ALTER TABLE episodes ADD COLUMN content_version BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT ''Optimistic version for episode writing''',
  'SELECT ''content_version already exists'' AS migration_status');
PREPARE writing_migration FROM @writing_ddl;
EXECUTE writing_migration;
DEALLOCATE PREPARE writing_migration;

SET @writing_check_exists = (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'episodes'
    AND CONSTRAINT_NAME = 'ck_episodes_content_version' AND CONSTRAINT_TYPE = 'CHECK'
);
SET @writing_ddl = IF(@writing_check_exists = 0,
  'ALTER TABLE episodes ADD CONSTRAINT ck_episodes_content_version CHECK (content_version > 0)',
  'SELECT ''ck_episodes_content_version already exists'' AS migration_status');
PREPARE writing_migration FROM @writing_ddl;
EXECUTE writing_migration;
DEALLOCATE PREPARE writing_migration;

-- Preserve all content, script rows/states, audit values, existing pointers and versions.
-- Restrict retries to untouched rows so a later intentional empty selection stays empty.
START TRANSACTION;
UPDATE episodes AS e
SET e.editing_script_id = (
  SELECT s.id FROM episode_scripts AS s WHERE s.episode_id = e.id
  ORDER BY (s.state = 'confirmed') DESC, s.position ASC, s.id ASC LIMIT 1
)
WHERE e.editing_script_id IS NULL AND e.content_version = 1;
COMMIT;
