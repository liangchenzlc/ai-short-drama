-- Explicit offline migration for the account assistant.

ALTER TABLE agent_conversations MODIFY COLUMN project_id BIGINT UNSIGNED NULL;

ALTER TABLE agent_conversations DROP CHECK ck_agent_conversations_scope, ADD CONSTRAINT ck_agent_conversations_scope CHECK ((scope_version = 3 AND project_id IS NULL AND stage IS NULL AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR (scope_version = 2 AND project_id IS NOT NULL AND stage IS NULL AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR (scope_version = 0 AND project_id IS NOT NULL AND stage IS NULL AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR (scope_version = 1 AND project_id IS NOT NULL AND stage IS NOT NULL AND subject_type IS NOT NULL AND subject_id IS NOT NULL AND subject_id > 0 AND task_type IS NOT NULL AND ((stage = 'source' AND subject_type = 'project' AND subject_id = project_id AND task_type = 'writing') OR (stage = 'assets' AND subject_type = 'project' AND subject_id = project_id AND task_type IN ('extraction','batch')) OR (stage = 'assets' AND subject_type = 'asset' AND task_type IN ('creation','image')) OR (stage = 'storyboard' AND subject_type = 'project' AND subject_id = project_id AND task_type IN ('planning','batch')) OR (stage = 'storyboard' AND subject_type = 'shot' AND task_type IN ('creation','image','video')))));

ALTER TABLE agent_conversations DROP CHECK ck_agent_conversations_status, ADD CONSTRAINT ck_agent_conversations_status CHECK (status IN ('active','archived','deleting','deleted'));

ALTER TABLE agent_conversations ADD INDEX idx_agent_conversations_account (owner_user_id, status, updated_at, id);

CREATE TABLE `agent_tasks` (
  `id` BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `goal` MEDIUMTEXT NOT NULL,
  `status` VARCHAR(24) NOT NULL DEFAULT 'queued',
  `phase` VARCHAR(24) NOT NULL DEFAULT 'at_boundary',
  `row_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `plan_revision` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `input_revision` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `applied_input_revision` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `control_seq` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `applied_control_seq` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `control_state` VARCHAR(24) NOT NULL DEFAULT 'none',
  `execution_target` JSON NULL,
  `memory_project_ids` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `context_snapshot` JSON NULL,
  `selected_skills` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `effective_state` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `media_limits` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `media_usage` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `error` JSON NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_tasks_control CHECK (control_state IN ('none','stop_pending','redirect_pending','delete_pending')),
  CONSTRAINT ck_agent_tasks_goal CHECK (CHAR_LENGTH(TRIM(goal)) > 0),
  CONSTRAINT ck_agent_tasks_objects CHECK (JSON_TYPE(memory_project_ids) = 'ARRAY' AND JSON_TYPE(selected_skills) = 'ARRAY' AND JSON_TYPE(effective_state) = 'OBJECT' AND JSON_TYPE(media_limits) = 'OBJECT' AND JSON_TYPE(media_usage) = 'OBJECT'),
  CONSTRAINT ck_agent_tasks_phase CHECK (phase IN ('deciding','tool_running','waiting_media','settling_result','reconciling','at_boundary','terminal')),
  CONSTRAINT ck_agent_tasks_status CHECK (status IN ('queued','running','waiting_media','succeeded','stopped','paused_error','paused_budget')),
  CONSTRAINT ck_agent_tasks_versions CHECK (row_version > 0 AND plan_revision > 0 AND input_revision >= applied_input_revision AND control_seq >= applied_control_seq),
  CONSTRAINT fk_agent_tasks_conversation FOREIGN KEY (conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_tasks_parent` (id, conversation_id),
  KEY `idx_agent_tasks_queue` (`conversation_id`, `status`, `id`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

ALTER TABLE agent_messages ADD COLUMN task_id BIGINT UNSIGNED NULL, ADD INDEX idx_agent_messages_task (task_id, seq), ADD CONSTRAINT fk_agent_messages_task FOREIGN KEY (task_id, conversation_id) REFERENCES agent_tasks (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE agent_runs ADD COLUMN task_id BIGINT UNSIGNED NULL, ADD INDEX idx_agent_runs_task (task_id, id), ADD CONSTRAINT fk_agent_runs_task FOREIGN KEY (task_id, conversation_id) REFERENCES agent_tasks (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE `agent_task_inputs` (
  `id` BIGINT UNSIGNED NOT NULL,
  `task_id` BIGINT UNSIGNED NOT NULL,
  `message_id` BIGINT UNSIGNED NOT NULL,
  `input_revision` BIGINT UNSIGNED NOT NULL,
  `payload` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `applied_plan_revision` BIGINT UNSIGNED NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_task_inputs_payload CHECK (JSON_TYPE(payload) = 'OBJECT'),
  CONSTRAINT ck_agent_task_inputs_revision CHECK (input_revision > 0),
  CONSTRAINT fk_agent_task_inputs_message FOREIGN KEY (message_id) REFERENCES agent_messages (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_task_inputs_task FOREIGN KEY (task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_task_inputs_message` (message_id),
  UNIQUE KEY `uk_agent_task_inputs_revision` (task_id, input_revision)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_task_controls` (
  `id` BIGINT UNSIGNED NOT NULL,
  `task_id` BIGINT UNSIGNED NOT NULL,
  `seq` BIGINT UNSIGNED NOT NULL,
  `kind` VARCHAR(16) NOT NULL,
  `request_key` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `request_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `expected_control_seq` BIGINT UNSIGNED NULL,
  `source_message_id` BIGINT UNSIGNED NULL,
  `requested_after_operation_id` BIGINT UNSIGNED NULL,
  `applied_after_operation_id` BIGINT UNSIGNED NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `applied_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_task_controls_kind CHECK (kind IN ('stop','redirect','resume','delete')),
  CONSTRAINT ck_agent_task_controls_seq CHECK (seq > 0),
  CONSTRAINT fk_agent_task_controls_message FOREIGN KEY (source_message_id) REFERENCES agent_messages (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_task_controls_task FOREIGN KEY (task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_task_controls_request` (task_id, request_key),
  UNIQUE KEY `uk_agent_task_controls_seq` (task_id, seq)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE `agent_operations` (
  `id` BIGINT UNSIGNED NOT NULL,
  `task_id` BIGINT UNSIGNED NOT NULL,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `tool_call_id` BIGINT UNSIGNED NOT NULL,
  `tool_name` VARCHAR(80) NOT NULL,
  `tool_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `plan_revision` BIGINT UNSIGNED NOT NULL,
  `input_revision` BIGINT UNSIGNED NOT NULL,
  `payload_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `arguments` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `status` VARCHAR(24) NOT NULL DEFAULT 'running',
  `phase` VARCHAR(24) NOT NULL DEFAULT 'tool_running',
  `row_version` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `execution_epoch` BIGINT UNSIGNED NOT NULL DEFAULT 1,
  `active_task_id` BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN status IN ('running','waiting_media','reconciling') THEN task_id ELSE NULL END) STORED NULL,
  `result` JSON NULL,
  `result_refs` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `changes` JSON NOT NULL DEFAULT (JSON_ARRAY()),
  `error` JSON NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `finished_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_operations_status CHECK (status IN ('running','waiting_media','reconciling','succeeded','failed','conflict')),
  CONSTRAINT ck_agent_operations_terminal CHECK ((status IN ('succeeded','failed','conflict')) = (finished_at IS NOT NULL)),
  CONSTRAINT ck_agent_operations_versions CHECK (tool_version > 0 AND plan_revision > 0 AND row_version > 0 AND execution_epoch > 0),
  CONSTRAINT fk_agent_operations_call FOREIGN KEY (tool_call_id) REFERENCES agent_tool_calls (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_operations_run FOREIGN KEY (run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_operations_task FOREIGN KEY (task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_operations_active` (active_task_id),
  UNIQUE KEY `uk_agent_operations_call` (tool_call_id),
  KEY `idx_agent_operations_task` (`task_id`, `id`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
