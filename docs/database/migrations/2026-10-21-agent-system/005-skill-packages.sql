-- Explicit offline migration for the account assistant.

ALTER TABLE agent_skills ADD COLUMN create_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL;

ALTER TABLE agent_skills ADD COLUMN create_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL;

ALTER TABLE agent_skills ADD CONSTRAINT ck_agent_skill_create_pair CHECK ((create_key IS NULL) = (create_hash IS NULL));

ALTER TABLE agent_skills ADD CONSTRAINT uk_agent_skill_create UNIQUE (owner_user_id, create_key);

ALTER TABLE agent_skills ADD CONSTRAINT uk_agent_skill_owner_identity UNIQUE (id, owner_user_id);

CREATE TABLE `agent_skill_settings` (
  `id` BIGINT UNSIGNED NOT NULL,
  `owner_user_id` BIGINT UNSIGNED NOT NULL,
  `skill_key` VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL,
  `skill_id` BIGINT UNSIGNED NULL,
  `enabled` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `invocation_mode` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'auto',
  `row_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_skill_setting_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_agent_skill_setting_identity CHECK (skill_id IS NULL OR skill_key = CONCAT('', skill_id)),
  CONSTRAINT ck_agent_skill_setting_values CHECK (invocation_mode IN ('auto','manual') AND row_version > 0),
  CONSTRAINT fk_agent_skill_setting_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_skill_setting_skill FOREIGN KEY (skill_id, owner_user_id) REFERENCES agent_skills (id, owner_user_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_skill_setting` (owner_user_id, skill_key)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_skill_versions` (
  `id` BIGINT UNSIGNED NOT NULL,
  `skill_key` VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL,
  `skill_id` BIGINT UNSIGNED NULL,
  `owner_user_id` BIGINT UNSIGNED NULL,
  `content_version` BIGINT UNSIGNED NOT NULL,
  `package_metadata` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `instructions` MEDIUMTEXT NOT NULL,
  `resources` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `checksum_sha256` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_skill_version_content CHECK (CHAR_LENGTH(TRIM(instructions)) > 0 AND OCTET_LENGTH(instructions) <= 65536),
  CONSTRAINT ck_agent_skill_version_identity CHECK ((skill_id IS NULL AND owner_user_id IS NULL) OR (skill_id IS NOT NULL AND owner_user_id IS NOT NULL AND skill_key = CONCAT('', skill_id))),
  CONSTRAINT ck_agent_skill_version_package CHECK (JSON_TYPE(package_metadata) = 'OBJECT' AND JSON_TYPE(resources) = 'ARRAY' AND JSON_LENGTH(resources) <= 31),
  CONSTRAINT ck_agent_skill_version_positive CHECK (content_version > 0),
  CONSTRAINT fk_agent_skill_version_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_skill_version_skill FOREIGN KEY (skill_id, owner_user_id) REFERENCES agent_skills (id, owner_user_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_skill_version` (skill_key, content_version),
  KEY `idx_agent_skill_version_owner` (`owner_user_id`, `skill_id`, `content_version`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

ALTER TABLE agent_skill_versions DROP CHECK ck_agent_skill_version_identity, ADD CONSTRAINT ck_agent_skill_version_identity CHECK ((skill_id IS NULL AND owner_user_id IS NULL) OR (skill_id IS NOT NULL AND owner_user_id IS NOT NULL AND skill_key = CONCAT('', skill_id)));

ALTER TABLE agent_skill_settings DROP CHECK ck_agent_skill_setting_identity, ADD CONSTRAINT ck_agent_skill_setting_identity CHECK (skill_id IS NULL OR skill_key = CONCAT('', skill_id));
