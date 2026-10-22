-- 创建生成媒体资产表，依赖 ai_generation_records、media_files。
-- MySQL 8.0.21+；先阅读 README.md，按编号执行一次。
-- 已成功执行的文件不要重复执行；不删除或覆盖已有对象。

USE ai_short_drama;
SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SET time_zone = '+00:00';

CREATE TABLE media_assets (
  id BIGINT UNSIGNED NOT NULL,
  record_id BIGINT UNSIGNED NOT NULL,
  output_index INT UNSIGNED NOT NULL,
  media_id BIGINT UNSIGNED NOT NULL,
  media_type VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL,
  name VARCHAR(255) NOT NULL,
  row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uk_media_assets_record_output (record_id, output_index),
  UNIQUE KEY uk_media_assets_media (media_id),
  KEY idx_media_assets_type_time (media_type, created_at, id),
  CONSTRAINT fk_media_assets_record FOREIGN KEY (record_id)
    REFERENCES ai_generation_records (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_media_assets_media FOREIGN KEY (media_id)
    REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_media_assets_type CHECK (media_type IN ('image','video')),
  CONSTRAINT ck_media_assets_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
  CONSTRAINT ck_media_assets_numbers CHECK (output_index > 0 AND row_version > 0),
  CONSTRAINT ck_media_assets_time CHECK (updated_at >= created_at)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='生成图片视频结果与资产库';
