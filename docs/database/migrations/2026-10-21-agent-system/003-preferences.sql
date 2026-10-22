-- Explicit offline migration for the account assistant.

CREATE TABLE `agent_preferences` (
  `id` BIGINT UNSIGNED NOT NULL,
  `owner_user_id` BIGINT UNSIGNED NOT NULL,
  `media_generation_enabled` TINYINT UNSIGNED NOT NULL DEFAULT 1,
  `policy_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_preferences_media_enabled CHECK (media_generation_enabled IN (0, 1)),
  CONSTRAINT ck_agent_preferences_version CHECK (policy_version >= 1),
  CONSTRAINT fk_agent_preferences_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_preferences_owner` (owner_user_id)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
