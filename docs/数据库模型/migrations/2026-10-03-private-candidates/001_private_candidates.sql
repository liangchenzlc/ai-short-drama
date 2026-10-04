-- 停止 API、Scheduler、Worker 后执行。本迁移不猜测历史作者，不修改密钥。
ALTER TABLE episode_scripts
  ADD COLUMN published_at DATETIME(6) NULL COMMENT '明确采用为项目作品的时间；候选为空';

ALTER TABLE media_files
  ADD COLUMN published_at DATETIME(6) NULL COMMENT '明确采用或附加为项目作品的时间；私有输出为空';

ALTER TABLE asset_image_candidates
  ADD COLUMN created_by BIGINT UNSIGNED NULL COMMENT '候选创建人；无法证明的历史归属保持为空' AFTER media_id,
  DROP INDEX uk_asset_image_candidates_media,
  ADD UNIQUE KEY uk_asset_image_candidates_media (created_by, asset_id, media_id),
  ADD KEY idx_asset_image_candidates_owner (created_by, asset_id, created_at, id);
