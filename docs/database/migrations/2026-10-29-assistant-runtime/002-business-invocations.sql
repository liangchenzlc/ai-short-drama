-- Account-scoped business invocations; apply after 001-graph-and-scopes.sql.

CREATE TABLE business_tool_invocations (
	id BIGINT UNSIGNED NOT NULL,
	owner_user_id BIGINT UNSIGNED NOT NULL,
	tool_name VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	request_id VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	arguments_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	source VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	token_id BIGINT UNSIGNED,
	status VARCHAR(24) NOT NULL DEFAULT 'running',
	result JSON,
	result_refs JSON NOT NULL DEFAULT (JSON_ARRAY()),
	changes JSON NOT NULL DEFAULT (JSON_ARRAY()),
	created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
	finished_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_business_invocations_owner FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
	CONSTRAINT uk_business_invocations_request UNIQUE (owner_user_id, tool_name, request_id),
	CONSTRAINT ck_business_invocations_source CHECK (source IN ('assistant','mcp')),
	CONSTRAINT ck_business_invocations_status CHECK (status IN ('running','succeeded','reconciling')),
	CONSTRAINT ck_business_invocations_terminal CHECK ((status = 'succeeded') = (finished_at IS NOT NULL)),
	CONSTRAINT ck_business_invocations_objects CHECK (JSON_TYPE(result_refs) = 'ARRAY' AND JSON_TYPE(changes) = 'ARRAY')
)ENGINE=InnoDB CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX idx_business_invocations_owner ON business_tool_invocations (owner_user_id, id);

ALTER TABLE agent_operations
    ADD COLUMN business_invocation_id BIGINT UNSIGNED NULL AFTER tool_call_id,
    ADD CONSTRAINT fk_agent_operations_invocation FOREIGN KEY (business_invocation_id)
        REFERENCES business_tool_invocations (id) ON DELETE RESTRICT ON UPDATE RESTRICT;
