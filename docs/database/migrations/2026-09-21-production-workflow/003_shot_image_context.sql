DELIMITER $$
DROP PROCEDURE IF EXISTS migrate_shot_image_context$$
CREATE PROCEDURE migrate_shot_image_context()
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='shot_images' AND column_name='context_hash') THEN
    ALTER TABLE shot_images ADD COLUMN context_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL COMMENT '采用时的shot-context-v1摘要' AFTER model_id;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='shot_images' AND constraint_name='ck_shot_images_context_hash') THEN
    ALTER TABLE shot_images ADD CONSTRAINT ck_shot_images_context_hash CHECK (context_hash IS NULL OR CHAR_LENGTH(context_hash)=64);
  END IF;
END$$
CALL migrate_shot_image_context()$$
DROP PROCEDURE migrate_shot_image_context$$
DELIMITER ;
