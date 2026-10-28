CREATE TABLE IF NOT EXISTS `project_story_settings` (
  `project_id` BIGINT UNSIGNED NOT NULL,
  `row_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `logline` VARCHAR(200) NOT NULL DEFAULT '',
  `synopsis` MEDIUMTEXT NOT NULL,
  `genre` VARCHAR(100) NOT NULL DEFAULT '',
  `character_bios` MEDIUMTEXT NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`project_id`),
  CONSTRAINT ck_story_settings_time CHECK (updated_at >= created_at),
  CONSTRAINT ck_story_settings_version CHECK (row_version > 0),
  CONSTRAINT fk_story_settings_project FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC CHARSET=utf8mb4 COMMENT='项目故事设定；独立版本，不修改剧本或历史大纲' COLLATE utf8mb4_0900_ai_ci;
