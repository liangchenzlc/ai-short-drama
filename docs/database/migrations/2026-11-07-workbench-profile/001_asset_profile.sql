-- Additive migration for the confirmed single-workbench asset profile.
ALTER TABLE `assets`
  ADD COLUMN `profile` JSON NOT NULL DEFAULT (JSON_OBJECT()) COMMENT '素材类型专属资料'
  AFTER `description`;
