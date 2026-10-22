ALTER TABLE `ai_model_configs` DROP CHECK `ck_ai_service_type`, ADD CONSTRAINT `ck_ai_service_type` CHECK (`service_type` IN ('text', 'image', 'video', 'audio'));

ALTER TABLE `async_tasks` DROP CHECK `ck_async_tasks_type`, ADD CONSTRAINT `ck_async_tasks_type` CHECK (service_type IN ('text','image','video','audio'));

ALTER TABLE `media_assets` DROP CHECK `ck_media_assets_type`, ADD CONSTRAINT `ck_media_assets_type` CHECK (media_type IN ('image','video','audio'));

ALTER TABLE `media_files` DROP CHECK `ck_media_format`, ADD CONSTRAINT `ck_media_format` CHECK (`format_code` = 'demo:image' OR `format_code` LIKE 'image/%' OR `format_code` LIKE 'video/%' OR `format_code` LIKE 'audio/%');

ALTER TABLE `media_files` DROP CHECK `ck_media_duration`, ADD CONSTRAINT `ck_media_duration` CHECK ((`duration_ms` IS NULL OR `duration_ms` > 0) AND (`format_code` LIKE 'video/%' OR `format_code` LIKE 'audio/%' OR `duration_ms` IS NULL));

CREATE TABLE `episode_sounds` (
  `assembly_id` BIGINT UNSIGNED NOT NULL,
  `row_version` BIGINT UNSIGNED NOT NULL,
  `document` JSON NOT NULL,
  `reviewed_timeline_hash` CHAR(64) CHARACTER SET ascii NULL,
  `last_receipt` JSON NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`assembly_id`),
  CONSTRAINT `ck_sounds_document` CHECK (JSON_TYPE(document) = 'OBJECT'),
  CONSTRAINT `ck_sounds_version` CHECK (row_version > 0),
  CONSTRAINT `fk_sounds_assembly` FOREIGN KEY (`assembly_id`) REFERENCES `episode_assemblies` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='';

CREATE TABLE `project_voice_defaults` (
  `project_id` BIGINT UNSIGNED NOT NULL,
  `row_version` BIGINT UNSIGNED NOT NULL,
  `voices` JSON NOT NULL,
  PRIMARY KEY (`project_id`),
  CONSTRAINT `ck_voice_defaults_version` CHECK (row_version > 0),
  CONSTRAINT `ck_voice_defaults_voices` CHECK (JSON_TYPE(voices) = 'OBJECT'),
  CONSTRAINT `fk_voice_defaults_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='';

CREATE TABLE `sound_media_references` (
  `assembly_id` BIGINT UNSIGNED NOT NULL,
  `media_id` BIGINT UNSIGNED NOT NULL,
  `proxy_media_id` BIGINT UNSIGNED NULL,
  PRIMARY KEY (`assembly_id`, `media_id`),
  CONSTRAINT `fk_sound_refs_assembly` FOREIGN KEY (`assembly_id`) REFERENCES `episode_assemblies` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_sound_refs_media` FOREIGN KEY (`media_id`) REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_sound_refs_proxy` FOREIGN KEY (`proxy_media_id`) REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='';
