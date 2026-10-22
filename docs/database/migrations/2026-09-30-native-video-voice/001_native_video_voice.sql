CREATE TABLE `project_sound_modes` (
  `project_id` BIGINT UNSIGNED NOT NULL,
  `row_version` BIGINT UNSIGNED NOT NULL,
  `mode` VARCHAR(16) NOT NULL,
  PRIMARY KEY (`project_id`),
  CONSTRAINT `ck_project_sound_mode` CHECK (mode IN ('legacy','native')),
  CONSTRAINT `ck_project_sound_mode_version` CHECK (row_version > 0),
  CONSTRAINT `fk_projectsoundmode_project_id` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='';

CREATE TABLE `character_voices` (
  `project_id` BIGINT UNSIGNED NOT NULL,
  `asset_id` BIGINT UNSIGNED NOT NULL,
  `row_version` BIGINT UNSIGNED NOT NULL,
  `record_id` BIGINT UNSIGNED NOT NULL,
  `media_id` BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (`project_id`, `asset_id`),
  CONSTRAINT `ck_character_voice_version` CHECK (row_version > 0),
  CONSTRAINT `fk_charactervoice_asset_id` FOREIGN KEY (`asset_id`) REFERENCES `assets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_charactervoice_media_id` FOREIGN KEY (`media_id`) REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_charactervoice_project_id` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_charactervoice_record_id` FOREIGN KEY (`record_id`) REFERENCES `ai_generation_records` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='';

CREATE TABLE `shot_dialogues` (
  `shot_id` BIGINT UNSIGNED NOT NULL,
  `row_version` BIGINT UNSIGNED NOT NULL,
  `document` JSON NOT NULL,
  `receipt` JSON NULL,
  PRIMARY KEY (`shot_id`),
  CONSTRAINT `ck_shot_dialogue_document` CHECK (JSON_TYPE(document) = 'OBJECT'),
  CONSTRAINT `ck_shot_dialogue_version` CHECK (row_version > 0),
  CONSTRAINT `fk_shotdialogue_shot_id` FOREIGN KEY (`shot_id`) REFERENCES `shot_scripts` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='';
