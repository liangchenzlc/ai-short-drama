-- 为现有 AI 配置表增加内部协议与能力缓存。
-- MySQL 8.0.21+；先阅读 README.md，按编号执行一次。
-- 已成功执行的文件不要重复执行；不删除或覆盖已有对象。

USE ai_short_drama;
SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SET time_zone = '+00:00';

ALTER TABLE ai_model_configs
  ADD COLUMN capability_cache JSON NULL
  COMMENT '系统内部协议与能力缓存，不由用户编辑';
