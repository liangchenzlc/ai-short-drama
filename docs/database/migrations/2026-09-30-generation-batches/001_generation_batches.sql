CREATE TABLE `generation_batches` (
  `id` BIGINT UNSIGNED NOT NULL,
  `scene` VARCHAR(24) NOT NULL,
  `config_id` BIGINT UNSIGNED NOT NULL,
  `config_version` BIGINT UNSIGNED NOT NULL,
  `scope` JSON NOT NULL,
  `status` VARCHAR(24) NOT NULL,
  `idempotency_key` VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL,
  `request_hash` CHAR(64) CHARACTER SET ascii NOT NULL,
  `retry_of_id` BIGINT UNSIGNED NULL,
  `created_at` DATETIME(6) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `ck_generation_batch_scene` CHECK (scene IN ('asset_image','shot_image','shot_video')),
  CONSTRAINT `ck_generation_batch_status` CHECK (status IN ('running','paused','needs_review','succeeded','partial','failed','cancelled')),
  CONSTRAINT `fk_batch_config` FOREIGN KEY (`config_id`) REFERENCES `ai_model_configs` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_generation_batch_key` (`idempotency_key`),
  KEY `idx_generation_batch_schedule` (`config_id`, `status`, `id`)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='None';

CREATE TABLE `generation_batch_items` (
  `id` BIGINT UNSIGNED NOT NULL,
  `batch_id` BIGINT UNSIGNED NOT NULL,
  `source_id` BIGINT UNSIGNED NOT NULL,
  `name` VARCHAR(255) NOT NULL,
  `task_id` BIGINT UNSIGNED NOT NULL,
  `status` VARCHAR(24) NOT NULL,
  `error` JSON NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `ck_batch_item_status` CHECK (status IN ('waiting','active','blocked','succeeded','failed','cancelled','needs_review')),
  CONSTRAINT `fk_batch_item_batch` FOREIGN KEY (`batch_id`) REFERENCES `generation_batches` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_batch_item_task` FOREIGN KEY (`task_id`) REFERENCES `async_tasks` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_batch_item_source` (`batch_id`, `source_id`),
  UNIQUE KEY `uk_batch_item_task` (`task_id`),
  KEY `idx_batch_item_source` (`source_id`, `status`)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='None';
