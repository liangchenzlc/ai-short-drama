-- Agent 加固 010：交付要求表 + 操作证据摘要。
-- 停写后执行；新库直接使用 docs/database/schema/schema.mysql8.sql，不叠加本增量。
--
-- 回填说明：旧任务的交付要求只存在于 agent_tasks.effective_state.task_contract，
-- 没有原文偏移。回填行使用 origin='agent'、state='confirmed'、
-- quote_start=quote_end=0（结构合法但表示"无精确偏移"），描述追加 [legacy] 标记。

CREATE TABLE IF NOT EXISTS agent_task_requirements (
  id BIGINT UNSIGNED NOT NULL,
  task_id BIGINT UNSIGNED NOT NULL,
  requirement_key VARCHAR(80) NOT NULL,
  description VARCHAR(500) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  origin VARCHAR(16) NOT NULL,
  state VARCHAR(16) NOT NULL DEFAULT 'draft',
  minimum_count BIGINT UNSIGNED NOT NULL DEFAULT 1,
  artifact_kind VARCHAR(32) NULL,
  asset_kind VARCHAR(32) NULL,
  source_message_id BIGINT UNSIGNED NOT NULL,
  quote_start BIGINT UNSIGNED NOT NULL DEFAULT 0,
  quote_end BIGINT UNSIGNED NOT NULL DEFAULT 0,
  definition_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  attempts BIGINT UNSIGNED NOT NULL DEFAULT 0,
  input_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uk_agent_task_requirements_key (task_id, requirement_key),
  KEY idx_agent_task_requirements_state (task_id, state),
  CONSTRAINT fk_agent_task_requirements_task FOREIGN KEY (task_id) REFERENCES agent_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_agent_task_requirements_message FOREIGN KEY (source_message_id) REFERENCES agent_messages (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_agent_task_requirements_kind CHECK (kind IN ('answer','read','write','image','skill','observation','contract')),
  CONSTRAINT ck_agent_task_requirements_origin CHECK (origin IN ('server_draft','server_derived','agent','user')),
  CONSTRAINT ck_agent_task_requirements_state CHECK (state IN ('draft','confirmed','frozen','superseded')),
  CONSTRAINT ck_agent_task_requirements_quote CHECK (quote_end >= quote_start),
  CONSTRAINT ck_agent_task_requirements_count CHECK (minimum_count >= 1),
  CONSTRAINT ck_agent_task_requirements_name CHECK (CHAR_LENGTH(TRIM(requirement_key)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci ROW_FORMAT=DYNAMIC;

INSERT INTO agent_task_requirements (
  id, task_id, requirement_key, description, kind, origin, state, minimum_count,
  artifact_kind, asset_kind, source_message_id, quote_start, quote_end,
  definition_hash, attempts, input_revision, created_at, updated_at
)
SELECT
  UUID_SHORT(),
  t.id,
  COALESCE(NULLIF(TRIM(req.requirement_key), ''), CONCAT('legacy-', req.rn)),
  LEFT(CONCAT(COALESCE(req.description, ''), ' [legacy]'), 500),
  CASE
    WHEN req.kind IN ('answer','read','write','image','skill','observation','contract')
    THEN req.kind ELSE 'write'
  END,
  'agent',
  'confirmed',
  GREATEST(COALESCE(req.minimum_count, 1), 1),
  NULLIF(req.artifact_kind, ''),
  NULLIF(req.asset_kind, ''),
  m.id,
  0,
  0,
  SHA2(CONCAT_WS('|', t.id, req.requirement_key, req.description, req.kind), 256),
  0,
  t.applied_input_revision,
  t.created_at,
  t.updated_at
FROM agent_tasks t
JOIN JSON_TABLE(
  JSON_EXTRACT(COALESCE(t.effective_state, JSON_OBJECT()), '$.task_contract.requirements'),
  '$[*]' COLUMNS (
    rn FOR ORDINALITY,
    requirement_key VARCHAR(80) PATH '$.key',
    description VARCHAR(500) PATH '$.description',
    kind VARCHAR(16) PATH '$.kind',
    minimum_count INT PATH '$.minimum_count',
    artifact_kind VARCHAR(32) PATH '$.artifact_kind',
    asset_kind VARCHAR(32) PATH '$.asset_kind',
    source_message_id VARCHAR(40) PATH '$.source_message_id'
  )
) req ON TRUE
JOIN agent_messages m ON m.id = CAST(req.source_message_id AS UNSIGNED)
WHERE JSON_TYPE(
    JSON_EXTRACT(COALESCE(t.effective_state, JSON_OBJECT()), '$.task_contract.requirements')
  ) = 'ARRAY'
  AND NOT EXISTS (
    SELECT 1 FROM agent_task_requirements existing WHERE existing.task_id = t.id
  );

ALTER TABLE agent_operations
  ADD COLUMN result_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER result,
  ADD COLUMN evidence_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER result_digest,
  ADD KEY idx_agent_operations_evidence (task_id, result_digest),
  ADD CONSTRAINT ck_agent_operations_evidence CHECK (
    evidence_kind IS NULL OR evidence_kind IN ('write','read','observation','skill','none')
  );
