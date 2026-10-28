SELECT DATABASE() AS target_database, VERSION() AS mysql_version;
SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_skill_settings';
SELECT tc.CONSTRAINT_NAME, cc.CHECK_CLAUSE
FROM information_schema.TABLE_CONSTRAINTS tc
JOIN information_schema.CHECK_CONSTRAINTS cc
  ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
WHERE tc.TABLE_SCHEMA = DATABASE() AND tc.TABLE_NAME = 'agent_skill_settings';
SELECT COUNT(*) AS invalid_settings FROM agent_skill_settings WHERE row_version <= 0 OR enabled NOT IN (0, 1);
SELECT COUNT(*) AS settings_count FROM agent_skill_settings;
SELECT COUNT(*) AS versions_count FROM agent_skill_versions;
