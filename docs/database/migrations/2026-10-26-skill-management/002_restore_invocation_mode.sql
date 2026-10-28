-- Stop new API and Workers first. Restore saved column values from the pre-DDL
-- backup by id, owner_user_id and skill_key before starting the old application.
ALTER TABLE agent_skill_settings
  DROP CHECK ck_agent_skill_setting_values,
  ADD COLUMN invocation_mode VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'auto' AFTER enabled,
  ADD CONSTRAINT ck_agent_skill_setting_values CHECK (invocation_mode IN ('auto','manual') AND row_version > 0);
