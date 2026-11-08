-- Personal video-reference selections contain asset identities or shot image IDs.
ALTER TABLE `user_creation_drafts`
  ADD COLUMN `video_references` JSON NOT NULL DEFAULT (JSON_ARRAY()) COMMENT '视频参考的素材或分镜图片身份'
    AFTER `reference_media_ids`,
  ADD CONSTRAINT `ck_user_creation_drafts_video_references`
    CHECK (JSON_TYPE(`video_references`) = 'ARRAY' AND JSON_LENGTH(`video_references`) <= 100);
