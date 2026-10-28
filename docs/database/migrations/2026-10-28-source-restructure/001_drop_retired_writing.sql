-- 先停止 API / scheduler / 全部 Worker，并执行 reset_retired_creation_data.py --execute。
-- 已获授权的测试业务数据直接清理；账号、模型、Skill 版本和资源、画布配置保留。
DROP TABLE IF EXISTS novel_script_records;
DROP TABLE IF EXISTS project_story_outlines;
DROP TABLE IF EXISTS project_novels;
