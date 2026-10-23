-- Conversation-scoped display order for messages and task operations.
-- Nullable on purpose: code writes it from here on, old rows are backfilled below.

ALTER TABLE `agent_conversations`
  ADD COLUMN `next_item_seq` BIGINT UNSIGNED NOT NULL DEFAULT 1 AFTER `next_event_seq`;

ALTER TABLE `agent_messages`
  ADD COLUMN `item_seq` BIGINT UNSIGNED NULL AFTER `seq`,
  ADD COLUMN `kind` VARCHAR(16) NOT NULL DEFAULT 'reply' AFTER `role`,
  ADD KEY `idx_agent_messages_item` (`conversation_id`, `item_seq`),
  ADD CONSTRAINT `ck_agent_messages_kind` CHECK (`kind` IN ('reply','narration','notice'));

ALTER TABLE `agent_operations`
  ADD COLUMN `item_seq` BIGINT UNSIGNED NULL AFTER `tool_name`,
  ADD KEY `idx_agent_operations_item` (`item_seq`);

-- Backfill: one ordering over messages and operations per conversation, so that
-- historical conversations render with the same interleaved timeline as new ones.
UPDATE `agent_messages` m
JOIN (
  SELECT kind, ref_id, ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY created_at, ref_id) AS rn
  FROM (
    SELECT conversation_id, 'm' AS kind, id AS ref_id, created_at FROM agent_messages
    UNION ALL
    SELECT r.conversation_id, 'o' AS kind, o.id AS ref_id, o.created_at
    FROM agent_operations o JOIN agent_runs r ON r.id = o.run_id
  ) u
) x ON x.kind = 'm' AND x.ref_id = m.id
SET m.item_seq = x.rn;

UPDATE `agent_operations` o
JOIN (
  SELECT kind, ref_id, ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY created_at, ref_id) AS rn
  FROM (
    SELECT conversation_id, 'm' AS kind, id AS ref_id, created_at FROM agent_messages
    UNION ALL
    SELECT r.conversation_id, 'o' AS kind, o.id AS ref_id, o.created_at
    FROM agent_operations o JOIN agent_runs r ON r.id = o.run_id
  ) u
) x ON x.kind = 'o' AND x.ref_id = o.id
SET o.item_seq = x.rn;

UPDATE `agent_conversations` c
LEFT JOIN (
  SELECT conversation_id, MAX(item_seq) AS max_seq FROM (
    SELECT conversation_id, item_seq FROM agent_messages WHERE item_seq IS NOT NULL
    UNION ALL
    SELECT r.conversation_id, o.item_seq
    FROM agent_operations o JOIN agent_runs r ON r.id = o.run_id
    WHERE o.item_seq IS NOT NULL
  ) t GROUP BY conversation_id
) s ON s.conversation_id = c.id
SET c.next_item_seq = COALESCE(s.max_seq, 0) + 1;
