-- 由 migrate_account_memory.py --apply 完成数据备份与清理后执行，不手动重复运行。
ALTER TABLE agent_preferences
  DROP CHECK ck_agent_preferences_calls,
  DROP CHECK ck_agent_preferences_concurrency,
  DROP CHECK ck_agent_preferences_tokens,
  DROP CHECK ck_agent_preferences_version,
  ADD COLUMN workbench_enabled TINYINT UNSIGNED NOT NULL DEFAULT 1,
  ADD COLUMN long_term_memory_enabled TINYINT UNSIGNED NOT NULL DEFAULT 1,
  ADD COLUMN memory_generation BIGINT UNSIGNED NOT NULL DEFAULT 0,
  DROP COLUMN policy_version,
  DROP COLUMN model_call_limit,
  DROP COLUMN output_token_limit,
  DROP COLUMN subagent_max_concurrency,
  DROP COLUMN default_media_limits,
  ADD CONSTRAINT ck_agent_preferences_switches CHECK
    (workbench_enabled IN (0,1) AND long_term_memory_enabled IN (0,1));

ALTER TABLE agent_tasks
  DROP CHECK ck_agent_tasks_objects,
  DROP COLUMN memory_project_ids,
  DROP COLUMN media_limits,
  ADD CONSTRAINT ck_agent_tasks_objects CHECK
    (JSON_TYPE(selected_skills)='ARRAY' AND JSON_TYPE(effective_state)='OBJECT'
     AND JSON_TYPE(media_usage)='OBJECT');

ALTER TABLE agent_messages ADD COLUMN memory_capture_generation BIGINT UNSIGNED NULL;

ALTER TABLE ai_model_configs
  ADD COLUMN create_key VARCHAR(64) COLLATE utf8mb4_0900_bin NULL,
  ADD COLUMN create_hash VARCHAR(64) COLLATE utf8mb4_0900_bin NULL,
  ADD CONSTRAINT uk_ai_model_create UNIQUE (owner_user_id,create_key);

ALTER TABLE mcp_access_tokens
  DROP CHECK ck_mcp_tokens_scopes,
  DROP COLUMN project_ids,
  ADD COLUMN create_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD COLUMN create_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL;
UPDATE mcp_access_tokens SET create_key=CONCAT('legacy:',id),
  create_hash=SHA2(CONCAT('retired:',id),256);
ALTER TABLE mcp_access_tokens
  MODIFY create_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  MODIFY create_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  ADD CONSTRAINT uk_mcp_tokens_create UNIQUE (owner_user_id,create_key),
  ADD CONSTRAINT ck_mcp_tokens_scopes CHECK
    (JSON_TYPE(tool_names)='ARRAY' AND JSON_LENGTH(tool_names)>0
     AND JSON_TYPE(allowed_ips)='ARRAY');

ALTER TABLE agent_memory_items
  DROP FOREIGN KEY fk_agent_memory_items_conversation,
  DROP FOREIGN KEY fk_agent_memory_items_project,
  DROP CHECK ck_agent_memory_scope,
  DROP INDEX uk_agent_memory_identity,
  DROP INDEX idx_agent_memory_catalog,
  DROP COLUMN scope_type,
  DROP COLUMN scope_key,
  DROP COLUMN conversation_id,
  DROP COLUMN project_id,
  ADD CONSTRAINT uk_agent_memory_identity UNIQUE (owner_user_id,kind,memory_key),
  ADD INDEX idx_agent_memory_catalog (owner_user_id,state,updated_at,id);

ALTER TABLE agent_memory_sources
  DROP FOREIGN KEY fk_agent_memory_sources_conversation,
  DROP FOREIGN KEY fk_agent_memory_sources_message,
  DROP CHECK ck_agent_memory_source,
  DROP INDEX uk_agent_memory_source,
  DROP INDEX idx_agent_memory_sources_conversation,
  DROP COLUMN conversation_id,
  DROP COLUMN message_id,
  DROP COLUMN source_seq,
  DROP COLUMN subject_keys,
  ADD CONSTRAINT uk_agent_memory_source UNIQUE (memory_id,content_version,source_hash),
  ADD CONSTRAINT ck_agent_memory_source CHECK (content_version>0);

ALTER TABLE agent_memory_forget_rules
  DROP INDEX idx_agent_memory_forget_identity,
  DROP COLUMN scope_type,
  DROP COLUMN scope_key,
  ADD INDEX idx_agent_memory_forget_identity (owner_user_id,kind,memory_key,active);
