-- 统一宿主模型运行配置：两列可空，原标准配置保持原行为。
-- 旧绑定增加迁移标记，用户清空高级配置后重入不能恢复旧目录。
-- 先选择目标数据库；从 AIModelConfig ORM 生成。
-- 安全重入及旧目录回填使用 scripts/host_model_runtime_migration.py --apply。
-- SQL 仅新增字段，不复制凭据；Python 在服务端加密回填且保留模型 ID。

ALTER TABLE ai_model_configs ADD COLUMN runtime_profile JSON;

ALTER TABLE ai_model_configs ADD COLUMN runtime_credentials_cipher MEDIUMTEXT;

ALTER TABLE canvas_channel_models ADD COLUMN runtime_migrated_at DATETIME(3);
