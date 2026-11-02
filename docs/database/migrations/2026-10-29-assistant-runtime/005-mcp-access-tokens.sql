-- Apply after 004-durable-waits-and-questions.sql. Existing audit provenance remains NULL.

CREATE TABLE `mcp_access_tokens` (
  `id` BIGINT UNSIGNED NOT NULL,
  `owner_user_id` BIGINT UNSIGNED NOT NULL,
  `name` VARCHAR(80) NOT NULL,
  `token_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `token_hint` VARCHAR(24) NOT NULL,
  `project_ids` JSON NOT NULL,
  `tool_names` JSON NOT NULL,
  `expires_at` DATETIME(6) NULL,
  `allowed_ips` JSON NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `deleted_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT ck_mcp_tokens_scopes CHECK (JSON_TYPE(project_ids) = 'ARRAY' AND JSON_LENGTH(project_ids) > 0 AND JSON_TYPE(tool_names) = 'ARRAY' AND JSON_LENGTH(tool_names) > 0 AND JSON_TYPE(allowed_ips) = 'ARRAY'),
  CONSTRAINT fk_mcp_access_tokens_owner_user_id FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_mcp_access_tokens_token_hash` (token_hash),
  KEY `idx_mcp_tokens_owner` (`owner_user_id`, `deleted_at`, `id`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

ALTER TABLE audit_events
  ADD COLUMN source VARCHAR(16) NULL,
  ADD COLUMN token_id BIGINT UNSIGNED NULL,
  ADD CONSTRAINT ck_audit_source CHECK (source IN ('browser','assistant','mcp')),
  ADD CONSTRAINT fk_audit_events_token_id FOREIGN KEY (token_id)
    REFERENCES mcp_access_tokens (id) ON DELETE RESTRICT ON UPDATE RESTRICT;
