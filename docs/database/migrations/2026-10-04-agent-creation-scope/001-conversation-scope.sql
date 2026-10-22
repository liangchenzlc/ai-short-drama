-- 会话对象范围增量：旧记录保留 scope_version=0、四个 scope 字段 NULL。
-- 从当前 AgentConversation ORM 生成；先选择目标数据库。
-- 活动 Run 索引改为非唯一：允许消息排队，制作执行由项目/会话锁串行准入。
-- 安全重入使用 scripts/agent_scope_migration.py --apply，勿盲目重复执行本 SQL。

ALTER TABLE agent_conversations ADD COLUMN stage VARCHAR(16) COLLATE utf8mb4_0900_bin;

ALTER TABLE agent_conversations ADD COLUMN subject_type VARCHAR(16) COLLATE utf8mb4_0900_bin;

ALTER TABLE agent_conversations ADD COLUMN subject_id BIGINT UNSIGNED;

ALTER TABLE agent_conversations ADD COLUMN task_type VARCHAR(16) COLLATE utf8mb4_0900_bin;

ALTER TABLE agent_conversations ADD COLUMN scope_version BIGINT UNSIGNED NOT NULL DEFAULT 0;

CREATE INDEX idx_agent_conversations_scope ON agent_conversations (owner_user_id, episode_id, scope_version, stage, subject_type, subject_id, task_type, status, updated_at, id);

ALTER TABLE agent_conversations ADD CONSTRAINT ck_agent_conversations_scope CHECK ((scope_version = 0 AND stage IS NULL AND subject_type IS NULL AND subject_id IS NULL AND task_type IS NULL) OR (scope_version = 1 AND stage IS NOT NULL AND subject_type IS NOT NULL AND subject_id IS NOT NULL AND subject_id > 0 AND task_type IS NOT NULL AND ((stage = 'source' AND subject_type = 'episode' AND subject_id = episode_id AND task_type = 'writing') OR (stage = 'assets' AND subject_type = 'episode' AND subject_id = episode_id AND task_type IN ('extraction','batch')) OR (stage = 'assets' AND subject_type = 'asset' AND task_type IN ('creation','image')) OR (stage = 'storyboard' AND subject_type = 'episode' AND subject_id = episode_id AND task_type IN ('planning','batch')) OR (stage = 'storyboard' AND subject_type = 'shot' AND task_type IN ('creation','image','video')))));

CREATE INDEX idx_agent_runs_active ON agent_runs (active_conversation_id);

DROP INDEX uk_agent_runs_active_conversation ON agent_runs;
