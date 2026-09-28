-- Run once on existing databases, before starting the new API and workers.
ALTER TABLE shot_scripts
  ADD COLUMN video_prompt MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '视频用户提示词；空值使用分镜默认内容' AFTER image_settings,
  ADD COLUMN video_settings JSON NULL DEFAULT NULL COMMENT '下一次视频设置' AFTER video_prompt,
  ADD CONSTRAINT ck_shot_scripts_video_settings CHECK (video_settings IS NULL OR JSON_TYPE(video_settings) = 'OBJECT');

ALTER TABLE shot_videos
  ADD COLUMN context_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
  ADD COLUMN first_frame_media_id BIGINT UNSIGNED NULL DEFAULT NULL,
  ADD KEY idx_shot_videos_first_frame (first_frame_media_id),
  ADD CONSTRAINT fk_shot_videos_first_frame FOREIGN KEY (first_frame_media_id)
    REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT;
