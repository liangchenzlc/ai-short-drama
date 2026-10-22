-- Explicit offline migration for the account assistant.

CREATE TABLE `agent_batch_undos` (
  `id` BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `task_id` BIGINT UNSIGNED NOT NULL,
  `operation_id` BIGINT UNSIGNED NOT NULL,
  `request_key` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `request_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `preview_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `selected_keys` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `changes` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `result_refs` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_undo_arrays CHECK (JSON_TYPE(selected_keys) = 'ARRAY' AND JSON_TYPE(changes) = 'ARRAY' AND JSON_TYPE(result_refs) = 'ARRAY'),
  CONSTRAINT fk_agent_undo_conversation FOREIGN KEY (conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_undo_operation FOREIGN KEY (operation_id) REFERENCES agent_operations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_undo_task FOREIGN KEY (task_id, conversation_id) REFERENCES agent_tasks (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_undo_request` (conversation_id, request_key),
  KEY `idx_agent_undo_operation` (`operation_id`, `id`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
