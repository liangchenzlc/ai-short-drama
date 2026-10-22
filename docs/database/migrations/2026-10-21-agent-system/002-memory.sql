-- Explicit offline migration for the account assistant.

ALTER TABLE agent_conversations ADD COLUMN summary_covers_until_seq BIGINT UNSIGNED NOT NULL DEFAULT 0;

ALTER TABLE agent_conversations ADD COLUMN active_summary_id BIGINT UNSIGNED NULL;

ALTER TABLE agent_conversations ADD COLUMN summary_generation BIGINT UNSIGNED NOT NULL DEFAULT 0;

CREATE TABLE `agent_conversation_summaries` (
  `id` BIGINT UNSIGNED NOT NULL,
  `owner_user_id` BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `from_seq` BIGINT UNSIGNED NOT NULL,
  `to_seq` BIGINT UNSIGNED NOT NULL,
  `base_summary_id` BIGINT UNSIGNED NULL,
  `base_generation` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `source_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `content` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `token_estimate` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `model_config_id` BIGINT UNSIGNED NULL,
  `usage` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `status` VARCHAR(16) NOT NULL DEFAULT 'ready',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `activated_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_summaries_content CHECK (JSON_TYPE(content) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT'),
  CONSTRAINT ck_agent_summaries_range CHECK (from_seq > 0 AND to_seq >= from_seq AND status IN ('ready','active','stale','invalidated')),
  CONSTRAINT fk_agent_summaries_base FOREIGN KEY (base_summary_id) REFERENCES agent_conversation_summaries (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_summaries_conversation FOREIGN KEY (conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_summaries_model FOREIGN KEY (model_config_id) REFERENCES ai_model_configs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_summaries_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_summaries_parent` (id, conversation_id),
  KEY `idx_agent_summaries_range` (`conversation_id`, `from_seq`, `to_seq`, `status`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_memory_items` (
  `id` BIGINT UNSIGNED NOT NULL,
  `owner_user_id` BIGINT UNSIGNED NOT NULL,
  `scope_type` VARCHAR(16) NOT NULL,
  `scope_key` VARCHAR(128) NOT NULL,
  `conversation_id` BIGINT UNSIGNED NULL,
  `project_id` BIGINT UNSIGNED NULL,
  `kind` VARCHAR(16) NOT NULL,
  `memory_key` VARCHAR(160) COLLATE utf8mb4_0900_bin NOT NULL,
  `title` VARCHAR(200) NOT NULL,
  `aliases` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `tags` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `content` MEDIUMTEXT NOT NULL,
  `search_text` MEDIUMTEXT NOT NULL,
  `content_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `state` VARCHAR(16) NOT NULL DEFAULT 'active',
  `origin` VARCHAR(16) NOT NULL DEFAULT 'explicit',
  `pinned` TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `last_user_edit_at` DATETIME(6) NULL,
  `hit_count` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `last_used_at` DATETIME(6) NULL,
  `create_key` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  `create_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_memory_arrays CHECK (JSON_TYPE(aliases) = 'ARRAY' AND JSON_TYPE(tags) = 'ARRAY'),
  CONSTRAINT ck_agent_memory_create_pair CHECK ((create_key IS NULL) = (create_hash IS NULL)),
  CONSTRAINT ck_agent_memory_kind CHECK (kind IN ('preference','correction','setting','decision','reference')),
  CONSTRAINT ck_agent_memory_scope CHECK ((scope_type = 'user' AND scope_key = 'user' AND conversation_id IS NULL AND project_id IS NULL) OR (scope_type = 'conversation' AND conversation_id IS NOT NULL AND project_id IS NULL AND scope_key = CONCAT(conversation_id)) OR (scope_type = 'project' AND project_id IS NOT NULL AND conversation_id IS NULL AND scope_key = CONCAT(project_id))),
  CONSTRAINT ck_agent_memory_state CHECK (state IN ('active','forgotten','superseded') AND origin IN ('explicit','extracted')),
  CONSTRAINT ck_agent_memory_values CHECK (content_version > 0 AND pinned IN (0,1) AND CHAR_LENGTH(TRIM(content)) > 0 AND CHAR_LENGTH(TRIM(title)) > 0),
  CONSTRAINT fk_agent_memory_items_conversation FOREIGN KEY (conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_memory_items_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_memory_items_project FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_memory_create` (owner_user_id, create_key),
  UNIQUE KEY `uk_agent_memory_identity` (owner_user_id, scope_type, scope_key, kind, memory_key),
  UNIQUE KEY `uk_agent_memory_owner` (id, owner_user_id),
  KEY `idx_agent_memory_catalog` (`owner_user_id`, `state`, `scope_type`, `scope_key`, `pinned`, `id`),
  FULLTEXT KEY `idx_agent_memory_fulltext` (`search_text`) WITH PARSER `ngram`
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_memory_forget_rules` (
  `id` BIGINT UNSIGNED NOT NULL,
  `owner_user_id` BIGINT UNSIGNED NOT NULL,
  `memory_id` BIGINT UNSIGNED NOT NULL,
  `scope_type` VARCHAR(16) NOT NULL,
  `scope_key` VARCHAR(128) NOT NULL,
  `kind` VARCHAR(16) NOT NULL,
  `memory_key` VARCHAR(160) COLLATE utf8mb4_0900_bin NOT NULL,
  `forget_version` BIGINT UNSIGNED NOT NULL,
  `source_fingerprints` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `active` TINYINT UNSIGNED NOT NULL DEFAULT 1,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `restored_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_memory_forget CHECK (forget_version > 0 AND active IN (0,1) AND JSON_TYPE(source_fingerprints) = 'ARRAY'),
  CONSTRAINT fk_agent_memory_forget_item FOREIGN KEY (memory_id, owner_user_id) REFERENCES agent_memory_items (id, owner_user_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_memory_forget_item` (memory_id),
  KEY `idx_agent_memory_forget_identity` (`owner_user_id`, `scope_type`, `scope_key`, `kind`, `memory_key`, `active`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_memory_revisions` (
  `id` BIGINT UNSIGNED NOT NULL,
  `memory_id` BIGINT UNSIGNED NOT NULL,
  `content_version` BIGINT UNSIGNED NOT NULL,
  `change` VARCHAR(16) NOT NULL,
  `snapshot` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_memory_revision CHECK (content_version > 0 AND `change` IN ('create','edit','forget','restore','extract','invalidate')),
  CONSTRAINT ck_agent_memory_revision_snapshot CHECK (JSON_TYPE(snapshot) = 'OBJECT'),
  CONSTRAINT fk_agent_memory_revisions_item FOREIGN KEY (memory_id) REFERENCES agent_memory_items (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_memory_revision` (memory_id, content_version)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_memory_sources` (
  `id` BIGINT UNSIGNED NOT NULL,
  `memory_id` BIGINT UNSIGNED NOT NULL,
  `content_version` BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `message_id` BIGINT UNSIGNED NULL,
  `source_seq` BIGINT UNSIGNED NOT NULL,
  `source_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `subject_keys` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_memory_source CHECK (content_version > 0 AND source_seq > 0 AND JSON_TYPE(subject_keys) = 'ARRAY'),
  CONSTRAINT fk_agent_memory_sources_conversation FOREIGN KEY (conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_memory_sources_item FOREIGN KEY (memory_id) REFERENCES agent_memory_items (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_memory_sources_message FOREIGN KEY (message_id, conversation_id) REFERENCES agent_messages (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_memory_source` (memory_id, content_version, conversation_id, source_seq),
  KEY `idx_agent_memory_sources_conversation` (`conversation_id`, `memory_id`, `content_version`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_memory_jobs` (
  `id` BIGINT UNSIGNED NOT NULL,
  `owner_user_id` BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `run_id` BIGINT UNSIGNED NULL,
  `kind` VARCHAR(16) NOT NULL,
  `request_key` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `from_seq` BIGINT UNSIGNED NOT NULL,
  `to_seq` BIGINT UNSIGNED NOT NULL,
  `base_summary_id` BIGINT UNSIGNED NULL,
  `base_generation` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `source_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `source_snapshot` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `provider_state` VARCHAR(16) NOT NULL DEFAULT 'not_started',
  `raw_result` JSON NULL,
  `usage` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `error` JSON NULL,
  `attempts` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `message_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `message_status` VARCHAR(16) NOT NULL DEFAULT 'queued',
  `publish_token` CHAR(32) NULL,
  `publish_until` DATETIME(6) NULL,
  `lease_token` CHAR(32) NULL,
  `lease_until` DATETIME(6) NULL,
  `next_run_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `finished_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_memory_job_content CHECK (JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT'),
  CONSTRAINT ck_agent_memory_job_kind CHECK (kind IN ('summary','extract','task_state','purge') AND from_seq > 0 AND to_seq >= from_seq),
  CONSTRAINT ck_agent_memory_job_message CHECK (message_version > 0 AND message_status IN ('queued','publishing','published','idle')),
  CONSTRAINT ck_agent_memory_job_status CHECK (status IN ('pending','running','succeeded','failed','stale','cancelled') AND provider_state IN ('not_started','sending','received','unknown')),
  CONSTRAINT fk_agent_memory_jobs_conversation FOREIGN KEY (conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_memory_jobs_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_memory_jobs_run FOREIGN KEY (run_id, conversation_id) REFERENCES agent_runs (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_memory_jobs_summary FOREIGN KEY (base_summary_id) REFERENCES agent_conversation_summaries (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_memory_job_request` (owner_user_id, request_key),
  KEY `idx_agent_memory_jobs_due` (`status`, `message_status`, `next_run_at`, `id`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

ALTER TABLE agent_conversations ADD CONSTRAINT fk_agent_conversations_summary FOREIGN KEY(active_summary_id, id) REFERENCES agent_conversation_summaries (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE agent_memory_items DROP CHECK ck_agent_memory_scope, ADD CONSTRAINT ck_agent_memory_scope CHECK ((scope_type = 'user' AND scope_key = 'user' AND conversation_id IS NULL AND project_id IS NULL) OR (scope_type = 'conversation' AND conversation_id IS NOT NULL AND project_id IS NULL AND scope_key = CONCAT(conversation_id)) OR (scope_type = 'project' AND project_id IS NOT NULL AND conversation_id IS NULL AND scope_key = CONCAT(project_id)));
