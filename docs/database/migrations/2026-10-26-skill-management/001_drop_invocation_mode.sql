-- Run only after preflight, backup and coordinated API/Worker shutdown.
-- A single atomic MySQL 8 DDL; no content, enabled or version data is rewritten.
ALTER TABLE agent_skill_settings
  DROP CHECK ck_agent_skill_setting_values,
  DROP COLUMN invocation_mode,
  ADD CONSTRAINT ck_agent_skill_setting_values CHECK (row_version > 0);
