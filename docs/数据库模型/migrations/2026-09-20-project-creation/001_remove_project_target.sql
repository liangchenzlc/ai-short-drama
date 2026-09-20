-- Select the intended database before execution. MySQL 8.0.21+.
-- Removes obsolete project duration values. Does not change video duration fields.
-- Safe to rerun; use the canonical schema directly for new databases.
SET @project_target_column_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'projects' AND COLUMN_NAME = 'target_ms'
);
SET @project_target_check_exists = (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'projects'
    AND CONSTRAINT_NAME = 'ck_projects_target' AND CONSTRAINT_TYPE = 'CHECK'
);
SET @project_target_ddl = CASE
  WHEN @project_target_column_exists = 1 AND @project_target_check_exists = 1
    THEN 'ALTER TABLE projects DROP CHECK ck_projects_target, DROP COLUMN target_ms'
  WHEN @project_target_column_exists = 1
    THEN 'ALTER TABLE projects DROP COLUMN target_ms'
  WHEN @project_target_check_exists = 1
    THEN 'ALTER TABLE projects DROP CHECK ck_projects_target'
  ELSE 'SELECT ''Project target duration already absent'' AS migration_status'
END;
PREPARE project_target_migration FROM @project_target_ddl;
EXECUTE project_target_migration;
DEALLOCATE PREPARE project_target_migration;

-- Both counts must be zero.
SELECT COUNT(*) AS remaining_target_columns FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'projects' AND COLUMN_NAME = 'target_ms';
SELECT COUNT(*) AS remaining_target_checks FROM information_schema.TABLE_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'projects'
  AND CONSTRAINT_NAME = 'ck_projects_target';
