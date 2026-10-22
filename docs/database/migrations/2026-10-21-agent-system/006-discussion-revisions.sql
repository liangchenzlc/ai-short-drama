-- Explicit offline migration for the account assistant.

ALTER TABLE agent_runs ADD COLUMN trigger_revision BIGINT UNSIGNED NOT NULL DEFAULT 0;

ALTER TABLE agent_runs DROP INDEX uk_agent_runs_trigger, ADD UNIQUE INDEX uk_agent_runs_trigger (trigger_message_id, trigger_revision);

ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_trigger_revision CHECK (trigger_revision = 0 OR task_id IS NOT NULL);

ALTER TABLE agent_messages ADD COLUMN row_version BIGINT UNSIGNED NOT NULL DEFAULT 1;

ALTER TABLE agent_messages ADD CONSTRAINT ck_agent_messages_version CHECK (row_version > 0);

CREATE TABLE `agent_discussion_revisions` (
  `id` BIGINT UNSIGNED NOT NULL,
  `conversation_id` BIGINT UNSIGNED NOT NULL,
  `source_message_id` BIGINT UNSIGNED NOT NULL,
  `source_version` BIGINT UNSIGNED NOT NULL,
  `reply_message_id` BIGINT UNSIGNED NOT NULL,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `kind` VARCHAR(16) NOT NULL,
  `request_key` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `request_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  `source_snapshot` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `reply_snapshot` JSON NOT NULL DEFAULT (JSON_OBJECT()),
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT ck_agent_discussion_kind CHECK (kind IN ('edit','regenerate')),
  CONSTRAINT ck_agent_discussion_snapshots CHECK (JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(reply_snapshot) = 'OBJECT'),
  CONSTRAINT ck_agent_discussion_version CHECK (source_version > 0),
  CONSTRAINT fk_agent_discussion_conversation FOREIGN KEY (conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_discussion_reply FOREIGN KEY (reply_message_id, conversation_id) REFERENCES agent_messages (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_discussion_run FOREIGN KEY (run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_discussion_source FOREIGN KEY (source_message_id, conversation_id) REFERENCES agent_messages (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  UNIQUE KEY `uk_agent_discussion_request` (conversation_id, request_key),
  KEY `idx_agent_discussion_reply` (`reply_message_id`, `id`),
  KEY `idx_agent_discussion_source` (`source_message_id`, `id`)
) ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;
