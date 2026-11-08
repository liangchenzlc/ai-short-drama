-- Additive migration for shot creation details.
ALTER TABLE `shot_scripts`
  ADD COLUMN `details` JSON NOT NULL DEFAULT (JSON_OBJECT()) COMMENT '镜头创作资料'
  AFTER `script`;
