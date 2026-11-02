-- 在 001、002 之后执行。实时媒体政策与非实时助手设置分别采用独立 CAS 版本。
ALTER TABLE agent_preferences
  DROP CHECK ck_agent_preferences_version,
  ADD COLUMN custom_instructions MEDIUMTEXT NULL,
  ADD COLUMN settings_version BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN model_call_limit INTEGER UNSIGNED NOT NULL DEFAULT 240,
  ADD COLUMN output_token_limit INTEGER UNSIGNED NOT NULL DEFAULT 2000000,
  ADD COLUMN subagent_max_concurrency TINYINT UNSIGNED NOT NULL DEFAULT 3,
  ADD COLUMN default_media_limits JSON NOT NULL DEFAULT (JSON_OBJECT()),
  ADD CONSTRAINT ck_agent_preferences_version CHECK (policy_version >= 0),
  ADD CONSTRAINT ck_agent_preferences_calls CHECK (model_call_limit BETWEEN 20 AND 2000),
  ADD CONSTRAINT ck_agent_preferences_tokens CHECK (output_token_limit BETWEEN 100000 AND 20000000),
  ADD CONSTRAINT ck_agent_preferences_concurrency CHECK (subagent_max_concurrency BETWEEN 1 AND 3);
