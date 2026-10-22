-- Additive Agent schema: 25 tables; preserves existing columns/data, adds missing named deferred foreign keys.
-- Generated from the current Agent domain models; select the intended database first.
-- Use scripts/agent_migration.py --apply for safe table-level reentry.

CREATE TABLE agent_preferences (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	media_generation_enabled TINYINT UNSIGNED NOT NULL DEFAULT 1,
	policy_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_preferences_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_preferences_owner UNIQUE (owner_user_id),
	CONSTRAINT ck_agent_preferences_media_enabled CHECK (media_generation_enabled IN (0, 1)),
	CONSTRAINT ck_agent_preferences_version CHECK (policy_version >= 1)
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE agent_skills (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	name VARCHAR(120) NOT NULL,
	filename VARCHAR(255) NOT NULL,
	instructions MEDIUMTEXT NOT NULL,
	content_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	checksum_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	enabled BIGINT UNSIGNED NOT NULL DEFAULT 1,
	create_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	create_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	deleted_at DATETIME(6),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_skill_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_skill_owner_identity UNIQUE (id, owner_user_id),
	CONSTRAINT uk_agent_skill_create UNIQUE (owner_user_id, create_key),
	CONSTRAINT ck_agent_skill_enabled CHECK (enabled IN (0,1)),
	CONSTRAINT ck_agent_skill_versions CHECK (row_version > 0 AND content_version > 0),
	CONSTRAINT ck_agent_skill_content CHECK (CHAR_LENGTH(TRIM(instructions)) > 0 AND OCTET_LENGTH(instructions) <= 65536),
	CONSTRAINT ck_agent_skill_time CHECK (updated_at >= created_at),
	CONSTRAINT ck_agent_skill_create_pair CHECK ((create_key IS NULL) = (create_hash IS NULL))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_skill_owner ON agent_skills (owner_user_id, deleted_at, id);

CREATE TABLE agent_conversations (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	project_id BIGINT UNSIGNED,
	stage VARCHAR(16) COLLATE utf8mb4_0900_bin,
	subject_type VARCHAR(16) COLLATE utf8mb4_0900_bin,
	subject_id BIGINT UNSIGNED,
	task_type VARCHAR(16) COLLATE utf8mb4_0900_bin,
	scope_version BIGINT UNSIGNED NOT NULL DEFAULT 0,
	title VARCHAR(120) NOT NULL DEFAULT '新对话',
	status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'active',
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	next_message_seq BIGINT UNSIGNED NOT NULL DEFAULT 1,
	next_event_seq BIGINT UNSIGNED NOT NULL DEFAULT 1,
	fixed_requirements JSON NOT NULL DEFAULT (JSON_OBJECT()),
	summary_covers_until_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
	active_summary_id BIGINT UNSIGNED,
	summary_generation BIGINT UNSIGNED NOT NULL DEFAULT 0,
	create_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	create_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_conversations_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_conversations_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_conversation_create UNIQUE (owner_user_id, create_key),
	CONSTRAINT ck_agent_conversations_scope CHECK ((scope_version = 3 AND project_id IS NULL AND stage IS NULL AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR (scope_version = 2 AND project_id IS NOT NULL AND stage IS NULL AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR (scope_version = 0 AND project_id IS NOT NULL AND stage IS NULL AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR (scope_version = 1 AND project_id IS NOT NULL AND stage IS NOT NULL AND subject_type IS NOT NULL AND subject_id IS NOT NULL AND subject_id > 0 AND task_type IS NOT NULL AND ((stage = 'source' AND subject_type = 'project' AND subject_id = project_id AND task_type = 'writing') OR (stage = 'assets' AND subject_type = 'project' AND subject_id = project_id AND task_type IN ('extraction','batch')) OR (stage = 'assets' AND subject_type = 'asset' AND task_type IN ('creation','image')) OR (stage = 'storyboard' AND subject_type = 'project' AND subject_id = project_id AND task_type IN ('planning','batch')) OR (stage = 'storyboard' AND subject_type = 'shot' AND task_type IN ('creation','image','video'))))),
	CONSTRAINT ck_agent_conversations_status CHECK (status IN ('active','archived','deleting','deleted')),
	CONSTRAINT ck_agent_conversations_numbers CHECK (row_version > 0 AND next_message_seq > 0 AND next_event_seq > 0),
	CONSTRAINT ck_agent_conversations_requirements CHECK (JSON_TYPE(fixed_requirements) = 'OBJECT'),
	CONSTRAINT ck_agent_conversations_title CHECK (CHAR_LENGTH(TRIM(title)) > 0),
	CONSTRAINT ck_agent_conversations_create_pair CHECK ((create_key IS NULL) = (create_hash IS NULL)),
	CONSTRAINT ck_agent_conversations_create_hash CHECK (create_key IS NULL OR (CHAR_LENGTH(create_key) = 64 AND CHAR_LENGTH(create_hash) = 64)),
	CONSTRAINT ck_agent_conversations_time CHECK (updated_at >= created_at)
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_conversations_account ON agent_conversations (owner_user_id, status, updated_at, id);

CREATE INDEX idx_agent_conversations_owner_creation ON agent_conversations (owner_user_id, project_id, updated_at, id);

CREATE INDEX idx_agent_conversations_project ON agent_conversations (project_id, owner_user_id, id);

CREATE INDEX idx_agent_conversations_project_chat ON agent_conversations (owner_user_id, project_id, scope_version, status, updated_at, id);

CREATE INDEX idx_agent_conversations_scope ON agent_conversations (owner_user_id, project_id, scope_version, stage, subject_type, subject_id, task_type, status, updated_at, id);

CREATE TABLE agent_skill_settings (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	skill_key VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL,
	skill_id BIGINT UNSIGNED,
	enabled BIGINT UNSIGNED NOT NULL DEFAULT 1,
	invocation_mode VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'auto',
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_skill_setting_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_skill_setting_skill FOREIGN KEY(skill_id, owner_user_id) REFERENCES agent_skills (id, owner_user_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_skill_setting UNIQUE (owner_user_id, skill_key),
	CONSTRAINT ck_agent_skill_setting_enabled CHECK (enabled IN (0,1)),
	CONSTRAINT ck_agent_skill_setting_values CHECK (invocation_mode IN ('auto','manual') AND row_version > 0),
	CONSTRAINT ck_agent_skill_setting_identity CHECK (skill_id IS NULL OR skill_key = CONCAT('', skill_id))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE agent_skill_versions (
	id BIGINT UNSIGNED NOT NULL,
	skill_key VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL,
	skill_id BIGINT UNSIGNED,
	owner_user_id BIGINT UNSIGNED,
	content_version BIGINT UNSIGNED NOT NULL,
	package_metadata JSON NOT NULL DEFAULT (JSON_OBJECT()),
	instructions MEDIUMTEXT NOT NULL,
	resources JSON NOT NULL DEFAULT (JSON_ARRAY()),
	checksum_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_skill_version_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_skill_version_skill FOREIGN KEY(skill_id, owner_user_id) REFERENCES agent_skills (id, owner_user_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_skill_version UNIQUE (skill_key, content_version),
	CONSTRAINT ck_agent_skill_version_positive CHECK (content_version > 0),
	CONSTRAINT ck_agent_skill_version_identity CHECK ((skill_id IS NULL AND owner_user_id IS NULL) OR (skill_id IS NOT NULL AND owner_user_id IS NOT NULL AND skill_key = CONCAT('', skill_id))),
	CONSTRAINT ck_agent_skill_version_package CHECK (JSON_TYPE(package_metadata) = 'OBJECT' AND JSON_TYPE(resources) = 'ARRAY' AND JSON_LENGTH(resources) <= 31),
	CONSTRAINT ck_agent_skill_version_content CHECK (CHAR_LENGTH(TRIM(instructions)) > 0 AND OCTET_LENGTH(instructions) <= 65536)
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_skill_version_owner ON agent_skill_versions (owner_user_id, skill_id, content_version);

CREATE TABLE agent_conversation_summaries (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	from_seq BIGINT UNSIGNED NOT NULL,
	to_seq BIGINT UNSIGNED NOT NULL,
	base_summary_id BIGINT UNSIGNED,
	base_generation BIGINT UNSIGNED NOT NULL DEFAULT 0,
	source_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	content JSON NOT NULL DEFAULT (JSON_OBJECT()),
	token_estimate BIGINT UNSIGNED NOT NULL DEFAULT 0,
	model_config_id BIGINT UNSIGNED,
	`usage` JSON NOT NULL DEFAULT (JSON_OBJECT()),
	status VARCHAR(16) NOT NULL DEFAULT 'ready',
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	activated_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_summaries_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_summaries_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_summaries_base FOREIGN KEY(base_summary_id) REFERENCES agent_conversation_summaries (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_summaries_model FOREIGN KEY(model_config_id) REFERENCES ai_model_configs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_summaries_parent UNIQUE (id, conversation_id),
	CONSTRAINT ck_agent_summaries_range CHECK (from_seq > 0 AND to_seq >= from_seq AND status IN ('ready','active','stale','invalidated')),
	CONSTRAINT ck_agent_summaries_content CHECK (JSON_TYPE(content) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_summaries_range ON agent_conversation_summaries (conversation_id, from_seq, to_seq, status);

CREATE TABLE agent_memory_items (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	scope_type VARCHAR(16) NOT NULL,
	scope_key VARCHAR(128) NOT NULL,
	conversation_id BIGINT UNSIGNED,
	project_id BIGINT UNSIGNED,
	kind VARCHAR(16) NOT NULL,
	memory_key VARCHAR(160) COLLATE utf8mb4_0900_bin NOT NULL,
	title VARCHAR(200) NOT NULL,
	aliases JSON NOT NULL DEFAULT (JSON_ARRAY()),
	tags JSON NOT NULL DEFAULT (JSON_ARRAY()),
	content MEDIUMTEXT NOT NULL,
	search_text MEDIUMTEXT NOT NULL,
	content_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	state VARCHAR(16) NOT NULL DEFAULT 'active',
	origin VARCHAR(16) NOT NULL DEFAULT 'explicit',
	pinned TINYINT UNSIGNED NOT NULL DEFAULT 0,
	last_user_edit_at DATETIME(6),
	hit_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
	last_used_at DATETIME(6),
	create_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	create_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_memory_items_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_memory_items_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_memory_items_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_memory_identity UNIQUE (owner_user_id, scope_type, scope_key, kind, memory_key),
	CONSTRAINT uk_agent_memory_create UNIQUE (owner_user_id, create_key),
	CONSTRAINT uk_agent_memory_owner UNIQUE (id, owner_user_id),
	CONSTRAINT ck_agent_memory_scope CHECK ((scope_type = 'user' AND scope_key = 'user' AND conversation_id IS NULL AND project_id IS NULL) OR (scope_type = 'conversation' AND conversation_id IS NOT NULL AND project_id IS NULL AND scope_key = CONCAT(conversation_id)) OR (scope_type = 'project' AND project_id IS NOT NULL AND conversation_id IS NULL AND scope_key = CONCAT(project_id))),
	CONSTRAINT ck_agent_memory_kind CHECK (kind IN ('preference','correction','setting','decision','reference')),
	CONSTRAINT ck_agent_memory_state CHECK (state IN ('active','forgotten','superseded') AND origin IN ('explicit','extracted')),
	CONSTRAINT ck_agent_memory_values CHECK (content_version > 0 AND pinned IN (0,1) AND CHAR_LENGTH(TRIM(content)) > 0 AND CHAR_LENGTH(TRIM(title)) > 0),
	CONSTRAINT ck_agent_memory_arrays CHECK (JSON_TYPE(aliases) = 'ARRAY' AND JSON_TYPE(tags) = 'ARRAY'),
	CONSTRAINT ck_agent_memory_create_pair CHECK ((create_key IS NULL) = (create_hash IS NULL))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_memory_catalog ON agent_memory_items (owner_user_id, state, scope_type, scope_key, pinned, id);

CREATE FULLTEXT INDEX idx_agent_memory_fulltext ON agent_memory_items (search_text) WITH PARSER ngram;

CREATE TABLE agent_tasks (
	id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	goal MEDIUMTEXT NOT NULL,
	status VARCHAR(24) NOT NULL DEFAULT 'queued',
	phase VARCHAR(24) NOT NULL DEFAULT 'at_boundary',
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	plan_revision BIGINT UNSIGNED NOT NULL DEFAULT 1,
	input_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
	applied_input_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
	control_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
	applied_control_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
	control_state VARCHAR(24) NOT NULL DEFAULT 'none',
	execution_target JSON,
	memory_project_ids JSON NOT NULL DEFAULT (JSON_ARRAY()),
	context_snapshot JSON,
	selected_skills JSON NOT NULL DEFAULT (JSON_ARRAY()),
	effective_state JSON NOT NULL DEFAULT (JSON_OBJECT()),
	media_limits JSON NOT NULL DEFAULT (JSON_OBJECT()),
	media_usage JSON NOT NULL DEFAULT (JSON_OBJECT()),
	error JSON,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_tasks_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_tasks_parent UNIQUE (id, conversation_id),
	CONSTRAINT ck_agent_tasks_status CHECK (status IN ('queued','running','waiting_media','succeeded','stopped','paused_error','paused_budget')),
	CONSTRAINT ck_agent_tasks_phase CHECK (phase IN ('deciding','tool_running','waiting_media','settling_result','reconciling','at_boundary','terminal')),
	CONSTRAINT ck_agent_tasks_control CHECK (control_state IN ('none','stop_pending','redirect_pending','delete_pending')),
	CONSTRAINT ck_agent_tasks_versions CHECK (row_version > 0 AND plan_revision > 0 AND input_revision >= applied_input_revision AND control_seq >= applied_control_seq),
	CONSTRAINT ck_agent_tasks_goal CHECK (CHAR_LENGTH(TRIM(goal)) > 0),
	CONSTRAINT ck_agent_tasks_objects CHECK (JSON_TYPE(memory_project_ids) = 'ARRAY' AND JSON_TYPE(selected_skills) = 'ARRAY' AND JSON_TYPE(effective_state) = 'OBJECT' AND JSON_TYPE(media_limits) = 'OBJECT' AND JSON_TYPE(media_usage) = 'OBJECT')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_tasks_queue ON agent_tasks (conversation_id, status, id);

CREATE TABLE agent_memory_forget_rules (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	memory_id BIGINT UNSIGNED NOT NULL,
	scope_type VARCHAR(16) NOT NULL,
	scope_key VARCHAR(128) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	memory_key VARCHAR(160) COLLATE utf8mb4_0900_bin NOT NULL,
	forget_version BIGINT UNSIGNED NOT NULL,
	source_fingerprints JSON NOT NULL DEFAULT (JSON_ARRAY()),
	active TINYINT UNSIGNED NOT NULL DEFAULT 1,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	restored_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_memory_forget_item FOREIGN KEY(memory_id, owner_user_id) REFERENCES agent_memory_items (id, owner_user_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_memory_forget_item UNIQUE (memory_id),
	CONSTRAINT ck_agent_memory_forget CHECK (forget_version > 0 AND active IN (0,1) AND JSON_TYPE(source_fingerprints) = 'ARRAY')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_memory_forget_identity ON agent_memory_forget_rules (owner_user_id, scope_type, scope_key, kind, memory_key, active);

CREATE TABLE agent_memory_revisions (
	id BIGINT UNSIGNED NOT NULL,
	memory_id BIGINT UNSIGNED NOT NULL,
	content_version BIGINT UNSIGNED NOT NULL,
	`change` VARCHAR(16) NOT NULL,
	snapshot JSON NOT NULL DEFAULT (JSON_OBJECT()),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_memory_revisions_item FOREIGN KEY(memory_id) REFERENCES agent_memory_items (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_memory_revision UNIQUE (memory_id, content_version),
	CONSTRAINT ck_agent_memory_revision CHECK (content_version > 0 AND `change` IN ('create','edit','forget','restore','extract','invalidate')),
	CONSTRAINT ck_agent_memory_revision_snapshot CHECK (JSON_TYPE(snapshot) = 'OBJECT')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE agent_messages (
	id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	task_id BIGINT UNSIGNED,
	seq BIGINT UNSIGNED NOT NULL,
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	`role` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL,
	content MEDIUMTEXT NOT NULL DEFAULT (''),
	`references` JSON NOT NULL DEFAULT (JSON_ARRAY()),
	artifacts JSON NOT NULL DEFAULT (JSON_ARRAY()),
	idempotency_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_messages_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_messages_task FOREIGN KEY(task_id, conversation_id) REFERENCES agent_tasks (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_messages_seq UNIQUE (conversation_id, seq),
	CONSTRAINT uk_agent_messages_request UNIQUE (conversation_id, idempotency_key),
	CONSTRAINT uk_agent_messages_parent UNIQUE (id, conversation_id),
	CONSTRAINT ck_agent_messages_seq CHECK (seq > 0),
	CONSTRAINT ck_agent_messages_version CHECK (row_version > 0),
	CONSTRAINT ck_agent_messages_role CHECK (role IN ('user','assistant')),
	CONSTRAINT ck_agent_messages_arrays CHECK (JSON_TYPE(`references`) = 'ARRAY' AND JSON_TYPE(artifacts) = 'ARRAY'),
	CONSTRAINT ck_agent_messages_content_size CHECK (OCTET_LENGTH(content) <= 1048576),
	CONSTRAINT ck_agent_messages_nonempty CHECK (CHAR_LENGTH(TRIM(content)) > 0 OR JSON_LENGTH(`references`) > 0 OR JSON_LENGTH(artifacts) > 0),
	CONSTRAINT ck_agent_messages_request_pair CHECK ((idempotency_key IS NULL) = (request_hash IS NULL)),
	CONSTRAINT ck_agent_messages_request_hash CHECK (idempotency_key IS NULL OR (role = 'user' AND CHAR_LENGTH(idempotency_key) = 64 AND CHAR_LENGTH(request_hash) = 64))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_messages_task ON agent_messages (task_id, seq);

CREATE TABLE agent_attachments (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	attached_message_id BIGINT UNSIGNED,
	kind VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL,
	name VARCHAR(255) NOT NULL,
	mime_type VARCHAR(127) NOT NULL,
	media_id BIGINT UNSIGNED,
	text_content MEDIUMTEXT,
	checksum_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	byte_size BIGINT UNSIGNED NOT NULL,
	input_metadata JSON NOT NULL DEFAULT (JSON_OBJECT()),
	create_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	create_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	deleted_at DATETIME(6),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_attachment_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_attachment_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_attachment_message FOREIGN KEY(attached_message_id) REFERENCES agent_messages (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_attachment_media FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_attachment_create UNIQUE (conversation_id, create_key),
	CONSTRAINT ck_agent_attachment_kind CHECK (kind IN ('text','image','video','audio')),
	CONSTRAINT ck_agent_attachment_content CHECK ((kind = 'text' AND text_content IS NOT NULL AND media_id IS NULL) OR (kind <> 'text' AND media_id IS NOT NULL)),
	CONSTRAINT ck_agent_attachment_metadata CHECK (JSON_TYPE(input_metadata) = 'OBJECT'),
	CONSTRAINT ck_agent_attachment_create_pair CHECK ((create_key IS NULL) = (create_hash IS NULL))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_attachment_conversation ON agent_attachments (conversation_id, id);

CREATE TABLE agent_memory_sources (
	id BIGINT UNSIGNED NOT NULL,
	memory_id BIGINT UNSIGNED NOT NULL,
	content_version BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	message_id BIGINT UNSIGNED,
	source_seq BIGINT UNSIGNED NOT NULL,
	source_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	subject_keys JSON NOT NULL DEFAULT (JSON_ARRAY()),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_memory_sources_item FOREIGN KEY(memory_id) REFERENCES agent_memory_items (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_memory_sources_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_memory_sources_message FOREIGN KEY(message_id, conversation_id) REFERENCES agent_messages (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_memory_source UNIQUE (memory_id, content_version, conversation_id, source_seq),
	CONSTRAINT ck_agent_memory_source CHECK (content_version > 0 AND source_seq > 0 AND JSON_TYPE(subject_keys) = 'ARRAY')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_memory_sources_conversation ON agent_memory_sources (conversation_id, memory_id, content_version);

CREATE TABLE agent_runs (
	id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	task_id BIGINT UNSIGNED,
	trigger_message_id BIGINT UNSIGNED NOT NULL,
	trigger_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
	initiated_by BIGINT UNSIGNED NOT NULL,
	model_config_id BIGINT UNSIGNED NOT NULL,
	status VARCHAR(24) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'queued',
	phase VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'model',
	active_conversation_id BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN status IN ('queued','running','waiting_generation','waiting_review') THEN conversation_id ELSE NULL END) STORED,
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	checkpoint_schema_version INTEGER UNSIGNED NOT NULL DEFAULT 1,
	checkpoint JSON NOT NULL DEFAULT (JSON_OBJECT()),
	config_snapshot JSON NOT NULL DEFAULT (JSON_OBJECT()),
	budget JSON NOT NULL DEFAULT (JSON_OBJECT()),
	`usage` JSON NOT NULL DEFAULT (JSON_OBJECT()),
	message_status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'pending',
	message_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	next_run_at DATETIME(6),
	lease_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin,
	lease_until DATETIME(6),
	cancel_requested INTEGER UNSIGNED NOT NULL DEFAULT 0,
	error JSON,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	started_at DATETIME(6),
	finished_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_runs_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_runs_task FOREIGN KEY(task_id, conversation_id) REFERENCES agent_tasks (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_runs_trigger_message FOREIGN KEY(trigger_message_id, conversation_id) REFERENCES agent_messages (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_runs_initiator FOREIGN KEY(initiated_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_runs_model FOREIGN KEY(model_config_id) REFERENCES ai_model_configs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_runs_trigger UNIQUE (trigger_message_id, trigger_revision),
	CONSTRAINT ck_agent_runs_trigger_revision CHECK (trigger_revision = 0 OR task_id IS NOT NULL),
	CONSTRAINT uk_agent_runs_parent UNIQUE (id, conversation_id),
	CONSTRAINT ck_agent_runs_status CHECK (status IN ('queued','running','waiting_generation','waiting_review','succeeded','failed','cancelled')),
	CONSTRAINT ck_agent_runs_phase CHECK (phase IN ('model','tools','wait')),
	CONSTRAINT ck_agent_runs_message_status CHECK (message_status IN ('pending','publishing','published','idle')),
	CONSTRAINT ck_agent_runs_versions CHECK (row_version > 0 AND message_version > 0 AND checkpoint_schema_version > 0),
	CONSTRAINT ck_agent_runs_cancel CHECK (cancel_requested IN (0,1)),
	CONSTRAINT ck_agent_runs_lease_pair CHECK ((lease_token IS NULL) = (lease_until IS NULL)),
	CONSTRAINT ck_agent_runs_objects CHECK (JSON_TYPE(checkpoint) = 'OBJECT' AND JSON_TYPE(config_snapshot) = 'OBJECT' AND JSON_TYPE(budget) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT'),
	CONSTRAINT ck_agent_runs_error CHECK (error IS NULL OR JSON_TYPE(error) = 'OBJECT'),
	CONSTRAINT ck_agent_runs_terminal_time CHECK ((status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)),
	CONSTRAINT ck_agent_runs_time CHECK (updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) AND (finished_at IS NULL OR finished_at >= created_at) AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_runs_active ON agent_runs (active_conversation_id);

CREATE INDEX idx_agent_runs_history ON agent_runs (conversation_id, created_at, id);

CREATE INDEX idx_agent_runs_lease ON agent_runs (lease_until, status, id);

CREATE INDEX idx_agent_runs_schedule ON agent_runs (message_status, next_run_at, id);

CREATE INDEX idx_agent_runs_task ON agent_runs (task_id, id);

CREATE TABLE agent_task_controls (
	id BIGINT UNSIGNED NOT NULL,
	task_id BIGINT UNSIGNED NOT NULL,
	seq BIGINT UNSIGNED NOT NULL,
	kind VARCHAR(16) NOT NULL,
	request_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	expected_control_seq BIGINT UNSIGNED,
	source_message_id BIGINT UNSIGNED,
	requested_after_operation_id BIGINT UNSIGNED,
	applied_after_operation_id BIGINT UNSIGNED,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	applied_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_task_controls_task FOREIGN KEY(task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_task_controls_message FOREIGN KEY(source_message_id) REFERENCES agent_messages (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_task_controls_seq UNIQUE (task_id, seq),
	CONSTRAINT uk_agent_task_controls_request UNIQUE (task_id, request_key),
	CONSTRAINT ck_agent_task_controls_seq CHECK (seq > 0),
	CONSTRAINT ck_agent_task_controls_kind CHECK (kind IN ('stop','redirect','resume','delete'))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE agent_task_inputs (
	id BIGINT UNSIGNED NOT NULL,
	task_id BIGINT UNSIGNED NOT NULL,
	message_id BIGINT UNSIGNED NOT NULL,
	input_revision BIGINT UNSIGNED NOT NULL,
	payload JSON NOT NULL DEFAULT (JSON_OBJECT()),
	applied_plan_revision BIGINT UNSIGNED,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_task_inputs_task FOREIGN KEY(task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_task_inputs_message FOREIGN KEY(message_id) REFERENCES agent_messages (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_task_inputs_revision UNIQUE (task_id, input_revision),
	CONSTRAINT uk_agent_task_inputs_message UNIQUE (message_id),
	CONSTRAINT ck_agent_task_inputs_revision CHECK (input_revision > 0),
	CONSTRAINT ck_agent_task_inputs_payload CHECK (JSON_TYPE(payload) = 'OBJECT')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE agent_discussion_revisions (
	id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	source_message_id BIGINT UNSIGNED NOT NULL,
	source_version BIGINT UNSIGNED NOT NULL,
	reply_message_id BIGINT UNSIGNED NOT NULL,
	run_id BIGINT UNSIGNED NOT NULL,
	kind VARCHAR(16) NOT NULL,
	request_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	source_snapshot JSON NOT NULL DEFAULT (JSON_OBJECT()),
	reply_snapshot JSON NOT NULL DEFAULT (JSON_OBJECT()),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_discussion_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_discussion_source FOREIGN KEY(source_message_id, conversation_id) REFERENCES agent_messages (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_discussion_reply FOREIGN KEY(reply_message_id, conversation_id) REFERENCES agent_messages (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_discussion_run FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_discussion_request UNIQUE (conversation_id, request_key),
	CONSTRAINT ck_agent_discussion_version CHECK (source_version > 0),
	CONSTRAINT ck_agent_discussion_kind CHECK (kind IN ('edit','regenerate')),
	CONSTRAINT ck_agent_discussion_snapshots CHECK (JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(reply_snapshot) = 'OBJECT')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_discussion_reply ON agent_discussion_revisions (reply_message_id, id);

CREATE INDEX idx_agent_discussion_source ON agent_discussion_revisions (source_message_id, id);

CREATE TABLE agent_events (
	id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	run_id BIGINT UNSIGNED,
	seq BIGINT UNSIGNED NOT NULL,
	event_type VARCHAR(64) COLLATE utf8mb4_0900_bin NOT NULL,
	payload JSON NOT NULL DEFAULT (JSON_OBJECT()),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_events_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_events_run FOREIGN KEY(run_id, conversation_id) REFERENCES agent_runs (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_events_seq UNIQUE (conversation_id, seq),
	CONSTRAINT ck_agent_events_required CHECK (seq > 0 AND CHAR_LENGTH(TRIM(event_type)) > 0),
	CONSTRAINT ck_agent_events_payload CHECK (JSON_TYPE(payload) = 'OBJECT')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_events_run ON agent_events (run_id, seq);

CREATE TABLE agent_memory_jobs (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	run_id BIGINT UNSIGNED,
	kind VARCHAR(16) NOT NULL,
	request_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	from_seq BIGINT UNSIGNED NOT NULL,
	to_seq BIGINT UNSIGNED NOT NULL,
	base_summary_id BIGINT UNSIGNED,
	base_generation BIGINT UNSIGNED NOT NULL DEFAULT 0,
	source_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	source_snapshot JSON NOT NULL DEFAULT (JSON_OBJECT()),
	status VARCHAR(16) NOT NULL DEFAULT 'pending',
	provider_state VARCHAR(16) NOT NULL DEFAULT 'not_started',
	raw_result JSON,
	`usage` JSON NOT NULL DEFAULT (JSON_OBJECT()),
	error JSON,
	attempts BIGINT UNSIGNED NOT NULL DEFAULT 0,
	message_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	message_status VARCHAR(16) NOT NULL DEFAULT 'queued',
	publish_token CHAR(32),
	publish_until DATETIME(6),
	lease_token CHAR(32),
	lease_until DATETIME(6),
	next_run_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	finished_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_memory_jobs_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_memory_jobs_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_memory_jobs_run FOREIGN KEY(run_id, conversation_id) REFERENCES agent_runs (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_memory_jobs_summary FOREIGN KEY(base_summary_id) REFERENCES agent_conversation_summaries (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_memory_job_request UNIQUE (owner_user_id, request_key),
	CONSTRAINT ck_agent_memory_job_kind CHECK (kind IN ('summary','extract','task_state','purge') AND from_seq > 0 AND to_seq >= from_seq),
	CONSTRAINT ck_agent_memory_job_status CHECK (status IN ('pending','running','succeeded','failed','stale','cancelled') AND provider_state IN ('not_started','sending','received','unknown')),
	CONSTRAINT ck_agent_memory_job_message CHECK (message_version > 0 AND message_status IN ('queued','publishing','published','idle')),
	CONSTRAINT ck_agent_memory_job_content CHECK (JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(`usage`) = 'OBJECT')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_memory_jobs_due ON agent_memory_jobs (status, message_status, next_run_at, id);

CREATE TABLE agent_turns (
	id BIGINT UNSIGNED NOT NULL,
	run_id BIGINT UNSIGNED NOT NULL,
	turn_no INTEGER UNSIGNED NOT NULL,
	status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'prepared',
	request_messages JSON NOT NULL DEFAULT (JSON_ARRAY()),
	response JSON,
	`usage` JSON NOT NULL DEFAULT (JSON_OBJECT()),
	provider_response_id VARCHAR(255) COLLATE utf8mb4_0900_bin,
	error JSON,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	started_at DATETIME(6),
	finished_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_turns_run FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_turns_number UNIQUE (run_id, turn_no),
	CONSTRAINT uk_agent_turns_parent UNIQUE (id, run_id),
	CONSTRAINT ck_agent_turns_number CHECK (turn_no > 0),
	CONSTRAINT ck_agent_turns_status CHECK (status IN ('prepared','sent','succeeded','failed','unknown')),
	CONSTRAINT ck_agent_turns_payload CHECK (JSON_TYPE(request_messages) = 'ARRAY' AND JSON_TYPE(`usage`) = 'OBJECT'),
	CONSTRAINT ck_agent_turns_result CHECK ((response IS NULL OR JSON_TYPE(response) = 'OBJECT') AND (error IS NULL OR JSON_TYPE(error) = 'OBJECT')),
	CONSTRAINT ck_agent_turns_time CHECK (updated_at >= created_at AND (started_at IS NULL OR started_at >= created_at) AND (finished_at IS NULL OR finished_at >= created_at) AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE agent_tool_calls (
	id BIGINT UNSIGNED NOT NULL,
	run_id BIGINT UNSIGNED NOT NULL,
	turn_id BIGINT UNSIGNED NOT NULL,
	call_index INTEGER UNSIGNED NOT NULL,
	provider_call_id VARCHAR(255) COLLATE utf8mb4_0900_bin NOT NULL,
	tool_name VARCHAR(80) COLLATE utf8mb4_0900_bin NOT NULL,
	arguments JSON NOT NULL DEFAULT (JSON_OBJECT()),
	arguments_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	idempotency_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	status VARCHAR(24) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'prepared',
	result JSON,
	generation_task_id BIGINT UNSIGNED,
	review_payload JSON,
	review_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
	review_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	review_decision VARCHAR(16) COLLATE utf8mb4_0900_bin,
	reviewed_by BIGINT UNSIGNED,
	reviewed_at DATETIME(6),
	error JSON,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_tool_calls_run FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_tool_calls_turn FOREIGN KEY(turn_id, run_id) REFERENCES agent_turns (id, run_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_tool_calls_generation FOREIGN KEY(generation_task_id) REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_tool_calls_reviewer FOREIGN KEY(reviewed_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_tool_calls_provider UNIQUE (turn_id, provider_call_id),
	CONSTRAINT uk_agent_tool_calls_order UNIQUE (turn_id, call_index),
	CONSTRAINT uk_agent_tool_calls_request UNIQUE (idempotency_key),
	CONSTRAINT uk_agent_tool_calls_generation UNIQUE (generation_task_id),
	CONSTRAINT ck_agent_tool_calls_numbers CHECK (call_index > 0 AND review_version > 0),
	CONSTRAINT ck_agent_tool_calls_required CHECK (CHAR_LENGTH(TRIM(provider_call_id)) > 0 AND CHAR_LENGTH(TRIM(tool_name)) > 0 AND CHAR_LENGTH(arguments_hash) = 64 AND CHAR_LENGTH(idempotency_key) = 64),
	CONSTRAINT ck_agent_tool_calls_status CHECK (status IN ('prepared','waiting_review','ready','running','waiting_generation','succeeded','failed','rejected','cancelled')),
	CONSTRAINT ck_agent_tool_calls_objects CHECK (JSON_TYPE(arguments) = 'OBJECT' AND (result IS NULL OR JSON_TYPE(result) = 'OBJECT') AND (error IS NULL OR JSON_TYPE(error) = 'OBJECT')),
	CONSTRAINT ck_agent_tool_calls_review_pair CHECK ((review_payload IS NULL) = (review_hash IS NULL)),
	CONSTRAINT ck_agent_tool_calls_review_payload CHECK (review_payload IS NULL OR (JSON_TYPE(review_payload) = 'OBJECT' AND CHAR_LENGTH(review_hash) = 64)),
	CONSTRAINT ck_agent_tool_calls_review_decision CHECK (review_decision IS NULL OR review_decision IN ('approved','rejected')),
	CONSTRAINT ck_agent_tool_calls_review_actor CHECK ((review_decision IS NULL AND reviewed_by IS NULL AND reviewed_at IS NULL) OR (review_decision IS NOT NULL AND review_payload IS NOT NULL AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
	CONSTRAINT ck_agent_tool_calls_review_pending CHECK (status <> 'waiting_review' OR (review_payload IS NOT NULL AND review_decision IS NULL)),
	CONSTRAINT ck_agent_tool_calls_time CHECK (updated_at >= created_at AND (reviewed_at IS NULL OR reviewed_at >= created_at))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_tool_calls_run_status ON agent_tool_calls (run_id, status, id);

CREATE TABLE agent_artifacts (
	id BIGINT UNSIGNED NOT NULL,
	project_id BIGINT UNSIGNED NOT NULL,
	tool_call_id BIGINT UNSIGNED NOT NULL,
	result_index INTEGER UNSIGNED NOT NULL,
	kind VARCHAR(32) COLLATE utf8mb4_0900_bin NOT NULL,
	status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'ready',
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	source_snapshot JSON NOT NULL DEFAULT (JSON_OBJECT()),
	source_content MEDIUMTEXT,
	metadata_json JSON NOT NULL DEFAULT (JSON_OBJECT()),
	proposed_patch JSON,
	script_id BIGINT UNSIGNED,
	parent_script_id BIGINT UNSIGNED,
	generation_task_id BIGINT UNSIGNED,
	media_asset_id BIGINT UNSIGNED,
	media_id BIGINT UNSIGNED,
	target_asset_id BIGINT UNSIGNED,
	target_shot_id BIGINT UNSIGNED,
	created_by BIGINT UNSIGNED NOT NULL,
	applied_by BIGINT UNSIGNED,
	applied_at DATETIME(6),
	apply_receipt JSON,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_artifacts_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_tool FOREIGN KEY(tool_call_id) REFERENCES agent_tool_calls (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_script FOREIGN KEY(script_id) REFERENCES project_scripts (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_parent_script FOREIGN KEY(parent_script_id) REFERENCES project_scripts (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_generation FOREIGN KEY(generation_task_id) REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_media_asset FOREIGN KEY(media_asset_id) REFERENCES media_assets (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_media FOREIGN KEY(media_id) REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_asset FOREIGN KEY(target_asset_id) REFERENCES assets (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_shot FOREIGN KEY(target_shot_id) REFERENCES shot_scripts (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_creator FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_artifacts_adopter FOREIGN KEY(applied_by) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_artifacts_result UNIQUE (tool_call_id, result_index),
	CONSTRAINT uk_agent_artifacts_script UNIQUE (script_id),
	CONSTRAINT ck_agent_artifacts_kind CHECK (kind IN ('novel_proposal','text_proposal','script_candidate','asset_patch','shot_patch','extraction_candidate','storyboard_candidate','image_candidate','video_candidate')),
	CONSTRAINT ck_agent_artifacts_status CHECK (status IN ('ready','applied','rejected','archived')),
	CONSTRAINT ck_agent_artifacts_numbers CHECK (row_version > 0 AND result_index > 0),
	CONSTRAINT ck_agent_artifacts_objects CHECK (JSON_TYPE(source_snapshot) = 'OBJECT' AND JSON_TYPE(metadata_json) = 'OBJECT' AND (proposed_patch IS NULL OR JSON_TYPE(proposed_patch) = 'OBJECT') AND (apply_receipt IS NULL OR JSON_TYPE(apply_receipt) = 'OBJECT')),
	CONSTRAINT ck_agent_artifacts_source_size CHECK (source_content IS NULL OR OCTET_LENGTH(source_content) <= 1048576),
	CONSTRAINT ck_agent_artifacts_text_proposal CHECK (kind NOT IN ('novel_proposal','text_proposal') OR (source_content IS NOT NULL AND CHAR_LENGTH(TRIM(source_content)) > 0)),
	CONSTRAINT ck_agent_artifacts_script_candidate CHECK (kind <> 'script_candidate' OR script_id IS NOT NULL),
	CONSTRAINT ck_agent_artifacts_asset_patch CHECK (kind <> 'asset_patch' OR (target_asset_id IS NOT NULL AND proposed_patch IS NOT NULL)),
	CONSTRAINT ck_agent_artifacts_shot_patch CHECK (kind <> 'shot_patch' OR (target_shot_id IS NOT NULL AND proposed_patch IS NOT NULL)),
	CONSTRAINT ck_agent_artifacts_adoption CHECK ((status = 'applied' AND applied_by IS NOT NULL AND applied_at IS NOT NULL AND apply_receipt IS NOT NULL) OR (status <> 'applied' AND applied_by IS NULL AND applied_at IS NULL AND apply_receipt IS NULL)),
	CONSTRAINT ck_agent_artifacts_time CHECK (updated_at >= created_at AND (applied_at IS NULL OR applied_at >= created_at))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_artifacts_creation ON agent_artifacts (project_id, kind, status, created_at, id);

CREATE INDEX idx_agent_artifacts_generation ON agent_artifacts (generation_task_id, id);

CREATE TABLE agent_operations (
	id BIGINT UNSIGNED NOT NULL,
	task_id BIGINT UNSIGNED NOT NULL,
	run_id BIGINT UNSIGNED NOT NULL,
	tool_call_id BIGINT UNSIGNED NOT NULL,
	tool_name VARCHAR(80) NOT NULL,
	tool_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	plan_revision BIGINT UNSIGNED NOT NULL,
	input_revision BIGINT UNSIGNED NOT NULL,
	payload_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	arguments JSON NOT NULL DEFAULT (JSON_OBJECT()),
	status VARCHAR(24) NOT NULL DEFAULT 'running',
	phase VARCHAR(24) NOT NULL DEFAULT 'tool_running',
	row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
	execution_epoch BIGINT UNSIGNED NOT NULL DEFAULT 1,
	active_task_id BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN status IN ('running','waiting_media','reconciling') THEN task_id ELSE NULL END) STORED,
	result JSON,
	result_refs JSON NOT NULL DEFAULT (JSON_ARRAY()),
	changes JSON NOT NULL DEFAULT (JSON_ARRAY()),
	error JSON,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	finished_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_operations_task FOREIGN KEY(task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_operations_run FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_operations_call FOREIGN KEY(tool_call_id) REFERENCES agent_tool_calls (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_operations_call UNIQUE (tool_call_id),
	CONSTRAINT uk_agent_operations_active UNIQUE (active_task_id),
	CONSTRAINT ck_agent_operations_status CHECK (status IN ('running','waiting_media','reconciling','succeeded','failed','conflict')),
	CONSTRAINT ck_agent_operations_terminal CHECK ((status IN ('succeeded','failed','conflict')) = (finished_at IS NOT NULL)),
	CONSTRAINT ck_agent_operations_versions CHECK (tool_version > 0 AND plan_revision > 0 AND row_version > 0 AND execution_epoch > 0)
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_operations_task ON agent_operations (task_id, id);

CREATE TABLE agent_batch_undos (
	id BIGINT UNSIGNED NOT NULL,
	conversation_id BIGINT UNSIGNED NOT NULL,
	task_id BIGINT UNSIGNED NOT NULL,
	operation_id BIGINT UNSIGNED NOT NULL,
	request_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	preview_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	selected_keys JSON NOT NULL DEFAULT (JSON_ARRAY()),
	changes JSON NOT NULL DEFAULT (JSON_ARRAY()),
	result_refs JSON NOT NULL DEFAULT (JSON_ARRAY()),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_undo_conversation FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_undo_task FOREIGN KEY(task_id, conversation_id) REFERENCES agent_tasks (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_undo_operation FOREIGN KEY(operation_id) REFERENCES agent_operations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_undo_request UNIQUE (conversation_id, request_key),
	CONSTRAINT ck_agent_undo_arrays CHECK (JSON_TYPE(selected_keys) = 'ARRAY' AND JSON_TYPE(changes) = 'ARRAY' AND JSON_TYPE(result_refs) = 'ARRAY')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_undo_operation ON agent_batch_undos (operation_id, id);

CREATE TABLE agent_media_requests (
	id BIGINT UNSIGNED NOT NULL,
	task_id BIGINT UNSIGNED NOT NULL,
	operation_id BIGINT UNSIGNED NOT NULL,
	item_index BIGINT UNSIGNED NOT NULL,
	media_kind VARCHAR(16) NOT NULL,
	requested_count BIGINT UNSIGNED NOT NULL,
	generation_task_id BIGINT UNSIGNED,
	frozen_target JSON NOT NULL DEFAULT (JSON_OBJECT()),
	frozen_request JSON NOT NULL DEFAULT (JSON_OBJECT()),
	reservation_state VARCHAR(16) NOT NULL,
	status VARCHAR(24) NOT NULL,
	result JSON,
	error JSON,
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_agent_media_requests_task FOREIGN KEY(task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_media_requests_operation FOREIGN KEY(operation_id) REFERENCES agent_operations (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT fk_agent_media_requests_generation FOREIGN KEY(generation_task_id) REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_agent_media_requests_item UNIQUE (operation_id, item_index),
	CONSTRAINT uk_agent_media_requests_generation UNIQUE (generation_task_id),
	CONSTRAINT ck_agent_media_kind CHECK (media_kind IN ('image','video','audio')),
	CONSTRAINT ck_agent_media_quantity CHECK (item_index > 0 AND requested_count BETWEEN 1 AND 4),
	CONSTRAINT ck_agent_media_reservation CHECK (reservation_state IN ('reserved','committed','released')),
	CONSTRAINT ck_agent_media_status CHECK (status IN ('reserved','accepted','unknown','succeeded','failed','conflict','not_submitted'))
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_media_requests_task ON agent_media_requests (task_id, id);

ALTER TABLE agent_conversations ADD CONSTRAINT fk_agent_conversations_summary FOREIGN KEY(active_summary_id, id) REFERENCES agent_conversation_summaries (id, conversation_id) ON DELETE RESTRICT ON UPDATE RESTRICT;
