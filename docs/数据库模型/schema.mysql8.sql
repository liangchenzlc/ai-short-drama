-- 短剧项目 MySQL 8 建表脚本
-- 版本要求：MySQL 8.0.21+
-- 执行前选定空数据库，连接使用 utf8mb4、UTC，并启用严格 SQL 模式（至少 STRICT_TRANS_TABLES）。
-- 按外键依赖顺序创建全部21张表；仅含 CREATE TABLE，不包含 USE、ALTER、数据修改或迁移操作。
-- 字段、索引及应用事务规则见同目录 MySQL8数据表设计.md。
-- 2026-09-20：合并模型生成三表与能力缓存，任务采用五种状态，项目已移除目标时长。

CREATE TABLE `projects` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `name` VARCHAR(120) NOT NULL COMMENT '项目名称，允许重名',
  `synopsis` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '故事梗概',
  `style` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '新分集默认风格',
  `aspect` VARCHAR(8) NOT NULL COMMENT '新分集默认画幅',
  `last_opened_at` DATETIME(6) NULL DEFAULT NULL COMMENT '最近打开时间',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  KEY `idx_projects_last_opened` (`last_opened_at`, `id`),
  CONSTRAINT `ck_projects_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_projects_name` CHECK (CHAR_LENGTH(TRIM(`name`)) > 0),
  CONSTRAINT `ck_projects_aspect` CHECK (`aspect` IN ('16:9', '9:16'))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='项目';

CREATE TABLE `ai_model_configs` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `service_type` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '文本 / 生图 / 生视频',
  `name` VARCHAR(120) NOT NULL COMMENT '配置显示名',
  `model_key` VARCHAR(255) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '供应商模型编码，保留大小写；每条配置仅保存一个模型编码',
  `provider` VARCHAR(120) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '供应商名称',
  `base_url` VARCHAR(2048) NOT NULL DEFAULT '' COMMENT '服务基础地址',
  `apikey` TEXT NULL DEFAULT NULL COMMENT '加密密钥信封（含算法/密钥版本/随机数/认证标签/密文的编码串），不存明文',
  `capability_cache` JSON NULL COMMENT '系统内部协议与能力缓存，不由用户编辑',
  `enabled` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '是否允许用于新操作',
  `is_deleted` TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '是否逻辑删除',
  `is_default` TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '是否本类型默认配置',
  `row_version` BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '沿用旧表；是否与其他表一并删除见文末',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  `default_service_type` VARCHAR(16) COLLATE utf8mb4_0900_bin GENERATED ALWAYS AS (CASE WHEN `is_default` = 1 AND `is_deleted` = 0 THEN `service_type` ELSE NULL END) STORED COMMENT '技术生成列：用于每类最多一个未删除默认配置；应用不写入',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ai_default_service` (`default_service_type`),
  KEY `idx_ai_type_available` (`service_type`, `is_deleted`, `enabled`),
  CONSTRAINT `ck_ai_model_configs_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_ai_service_type` CHECK (`service_type` IN ('text', 'image', 'video')),
  CONSTRAINT `ck_ai_required_text` CHECK (CHAR_LENGTH(TRIM(`name`)) > 0 AND CHAR_LENGTH(TRIM(`model_key`)) > 0 AND CHAR_LENGTH(TRIM(`provider`)) > 0),
  CONSTRAINT `ck_ai_flags` CHECK (`enabled` IN (0, 1) AND `is_deleted` IN (0, 1) AND `is_default` IN (0, 1)),
  CONSTRAINT `ck_ai_default_available` CHECK (`is_default` = 0 OR (`enabled` = 1 AND `is_deleted` = 0)),
  CONSTRAINT `ck_ai_version` CHECK (`row_version` > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='AI模型配置';

CREATE TABLE `media_files` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `format_code` VARCHAR(127) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '直接保存规范 MIME，例如 image/png、video/mp4；演示资源可用 demo:image',
  `storage_locator` VARCHAR(700) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '稳定存储定位值，不保存临时签名 URL 或 Blob URL',
  `original_name` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '原始文件名',
  `byte_size` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '实际文件字节数',
  `width` INT UNSIGNED NULL DEFAULT NULL COMMENT '实际像素宽度',
  `height` INT UNSIGNED NULL DEFAULT NULL COMMENT '实际像素高度',
  `duration_ms` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '实际视频时长，单位毫秒；图片为空',
  `checksum_sha256` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL COMMENT '文件校验值，不作为文件身份',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_media_locator` (`storage_locator`),
  CONSTRAINT `ck_media_files_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_media_locator` CHECK (CHAR_LENGTH(TRIM(`storage_locator`)) > 0),
  CONSTRAINT `ck_media_format` CHECK (`format_code` = 'demo:image' OR `format_code` LIKE 'image/%' OR `format_code` LIKE 'video/%'),
  CONSTRAINT `ck_media_dimensions` CHECK ((`width` IS NULL OR `width` > 0) AND (`height` IS NULL OR `height` > 0)),
  CONSTRAINT `ck_media_duration` CHECK ((`duration_ms` IS NULL OR `duration_ms` > 0) AND (`format_code` LIKE 'video/%' OR `duration_ms` IS NULL)),
  CONSTRAINT `ck_media_checksum` CHECK (`checksum_sha256` IS NULL OR CHAR_LENGTH(`checksum_sha256`) = 64)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='媒体元数据';

CREATE TABLE `episodes` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `project_id` BIGINT UNSIGNED NOT NULL COMMENT '所属项目',
  `position` INT UNSIGNED NOT NULL COMMENT '项目内分集排序',
  `title` VARCHAR(255) NOT NULL COMMENT '分集标题',
  `synopsis` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '分集简介',
  `aspect` VARCHAR(8) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '本集画幅',
  `style` VARCHAR(255) NOT NULL DEFAULT '' COMMENT '本集风格',
  `editing_script_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '当前编辑剧本；应用保证属于本集，不设循环外键',
  `content_version` BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '小说、剧本与编辑选择共用的乐观并发版本',
  `storyboard_version` BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '分镜增改、关联、排序、归档与采用共用的乐观并发版本',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_episodes_project_position` (`project_id`, `position`),
  CONSTRAINT `fk_episodes_project_id` FOREIGN KEY (`project_id`)
    REFERENCES `projects` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_episodes_position` CHECK (`position` > 0),
  CONSTRAINT `ck_episodes_content_version` CHECK (`content_version` > 0),
  CONSTRAINT `ck_episodes_storyboard_version` CHECK (`storyboard_version` > 0),
  CONSTRAINT `ck_episodes_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_episodes_title` CHECK (CHAR_LENGTH(TRIM(`title`)) > 0),
  CONSTRAINT `ck_episodes_aspect` CHECK (`aspect` IN ('16:9', '9:16'))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='分集';

CREATE TABLE `episode_novels` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `episode_id` BIGINT UNSIGNED NOT NULL COMMENT '所属分集',
  `content` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '分集详情中用户输入的当前小说正文',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_novels_episode` (`episode_id`),
  CONSTRAINT `fk_episode_novels_episode_id` FOREIGN KEY (`episode_id`)
    REFERENCES `episodes` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_episode_novels_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='小说原文';

CREATE TABLE `episode_scripts` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `episode_id` BIGINT UNSIGNED NOT NULL COMMENT '所属分集',
  `position` INT UNSIGNED NOT NULL COMMENT '同一分集内剧本排序，沿用原候选排序',
  `content` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '剧本正文',
  `state` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'unconfirmed' COMMENT '未确认 / 已确认',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  `confirmed_episode_id` BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN `state` = 'confirmed' THEN `episode_id` ELSE NULL END) STORED COMMENT '技术生成列：已确认时为分集ID，否则NULL；应用不写入',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_scripts_episode_position` (`episode_id`, `position`),
  UNIQUE KEY `uk_scripts_confirmed_episode` (`confirmed_episode_id`),
  CONSTRAINT `fk_episode_scripts_episode_id` FOREIGN KEY (`episode_id`)
    REFERENCES `episodes` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_episode_scripts_position` CHECK (`position` > 0),
  CONSTRAINT `ck_episode_scripts_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_scripts_state` CHECK (`state` IN ('unconfirmed', 'confirmed'))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='剧情（剧本）';

CREATE TABLE `assets` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `kind` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '角色 / 场景 / 道具',
  `name` VARCHAR(255) NOT NULL COMMENT '素材名称，允许重名',
  `label` VARCHAR(120) NOT NULL DEFAULT '' COMMENT '单个标签或分类文本，不存逗号分隔的多标签',
  `description` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '素材描述',
  `prompt` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '素材生成提示词',
  `model_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '使用的模型配置；手动建立、导入时可空',
  `media_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '当前素材图片，尚未生成时可空',
  `row_version` BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '素材元信息与采用图片的乐观并发版本',
  `state` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'unconfirmed' COMMENT '素材确认状态',
  `tags` JSON NOT NULL DEFAULT (JSON_ARRAY()) COMMENT '去空去重后的标签数组，最多20项',
  `scene_time` VARCHAR(60) NOT NULL DEFAULT '' COMMENT '场景时间；非场景必须为空',
  `creation_key` VARCHAR(128) COLLATE utf8mb4_0900_bin NULL DEFAULT NULL COMMENT '手动新建幂等键',
  `creation_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL COMMENT '初始创建请求摘要',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  KEY `idx_assets_kind_name` (`kind`, `name`),
  KEY `idx_assets_model_id` (`model_id`),
  KEY `idx_assets_media_id` (`media_id`),
  UNIQUE KEY `uk_assets_creation_key` (`creation_key`),
  CONSTRAINT `fk_assets_model_id` FOREIGN KEY (`model_id`)
    REFERENCES `ai_model_configs` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_assets_media_id` FOREIGN KEY (`media_id`)
    REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_assets_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_assets_kind` CHECK (`kind` IN ('character', 'scene', 'prop')),
  CONSTRAINT `ck_assets_name` CHECK (CHAR_LENGTH(TRIM(`name`)) > 0)
  ,CONSTRAINT `ck_assets_row_version` CHECK (`row_version` > 0)
  ,CONSTRAINT `ck_assets_state` CHECK (`state` IN ('unconfirmed', 'confirmed'))
  ,CONSTRAINT `ck_assets_confirmed_media` CHECK (`state` <> 'confirmed' OR `media_id` IS NOT NULL)
  ,CONSTRAINT `ck_assets_tags` CHECK (JSON_TYPE(`tags`) = 'ARRAY' AND JSON_LENGTH(`tags`) <= 20)
  ,CONSTRAINT `ck_assets_scene_time` CHECK (`kind` = 'scene' OR `scene_time` = '')
  ,CONSTRAINT `ck_assets_creation_pair` CHECK (
    (`creation_key` IS NULL AND `creation_hash` IS NULL)
    OR (`creation_key` IS NOT NULL AND CHAR_LENGTH(TRIM(`creation_key`)) > 0
      AND `creation_hash` IS NOT NULL AND CHAR_LENGTH(`creation_hash`) = 64)
  )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='素材';

CREATE TABLE `global_assets` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `asset_id` BIGINT UNSIGNED NOT NULL COMMENT '素材本体',
  `position` INT UNSIGNED NOT NULL COMMENT '全局库排序',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_global_assets_asset` (`asset_id`),
  UNIQUE KEY `uk_global_assets_position` (`position`),
  CONSTRAINT `fk_global_assets_asset_id` FOREIGN KEY (`asset_id`)
    REFERENCES `assets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_global_assets_position` CHECK (`position` > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='全部素材（全局库关联）';

CREATE TABLE `project_assets` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `project_id` BIGINT UNSIGNED NOT NULL COMMENT '项目',
  `asset_id` BIGINT UNSIGNED NOT NULL COMMENT '素材本体',
  `position` INT UNSIGNED NOT NULL COMMENT '项目内排序',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_project_assets_asset` (`project_id`, `asset_id`),
  UNIQUE KEY `uk_project_assets_position` (`project_id`, `position`),
  KEY `idx_project_assets_asset_id` (`asset_id`),
  CONSTRAINT `fk_project_assets_project_id` FOREIGN KEY (`project_id`)
    REFERENCES `projects` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_project_assets_asset_id` FOREIGN KEY (`asset_id`)
    REFERENCES `assets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_project_assets_position` CHECK (`position` > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='项目素材关联';

CREATE TABLE `episode_assets` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `episode_id` BIGINT UNSIGNED NOT NULL COMMENT '分集',
  `asset_id` BIGINT UNSIGNED NOT NULL COMMENT '素材本体',
  `position` INT UNSIGNED NOT NULL COMMENT '分集内排序',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_episode_assets_asset` (`episode_id`, `asset_id`),
  UNIQUE KEY `uk_episode_assets_position` (`episode_id`, `position`),
  KEY `idx_episode_assets_asset_id` (`asset_id`),
  CONSTRAINT `fk_episode_assets_episode_id` FOREIGN KEY (`episode_id`)
    REFERENCES `episodes` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_episode_assets_asset_id` FOREIGN KEY (`asset_id`)
    REFERENCES `assets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_episode_assets_position` CHECK (`position` > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='分集素材关联';

CREATE TABLE `shot_scripts` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `episode_id` BIGINT UNSIGNED NOT NULL COMMENT '所属分集',
  `position` INT UNSIGNED NOT NULL COMMENT '分集内镜头顺序',
  `script` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '分镜脚本正文',
  `duration_ms` INT UNSIGNED NOT NULL DEFAULT 3000 COMMENT '建议镜头时长（毫秒）',
  `source_excerpt` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '生成分镜的连续剧本原文依据',
  `row_version` BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '单镜头保存、归档与采用的乐观并发版本',
  `image_settings` JSON NULL DEFAULT NULL COMMENT '下一次生图设置：resolution/aspect/layout；NULL按默认值读取',
  `deleted_at` DATETIME(6) NULL DEFAULT NULL COMMENT '归档时间；非空行不参与活动分镜列表',
  `active_position` INT UNSIGNED GENERATED ALWAYS AS (CASE WHEN `deleted_at` IS NULL THEN `position` ELSE NULL END) STORED COMMENT '活动镜头顺序唯一键',
  `creation_key` VARCHAR(128) COLLATE utf8mb4_0900_bin NULL DEFAULT NULL COMMENT '手动新建幂等键',
  `creation_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL COMMENT '初始创建请求摘要',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_shots_episode_active_position` (`episode_id`, `active_position`),
  UNIQUE KEY `uk_shots_creation_key` (`creation_key`),
  UNIQUE KEY `uk_shots_id_episode` (`id`, `episode_id`),
  KEY `idx_shots_episode_deleted_position` (`episode_id`, `deleted_at`, `position`, `id`),
  CONSTRAINT `fk_shot_scripts_episode_id` FOREIGN KEY (`episode_id`)
    REFERENCES `episodes` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_shot_scripts_position` CHECK (`position` > 0),
  CONSTRAINT `ck_shot_scripts_duration_ms` CHECK (`duration_ms` BETWEEN 1000 AND 10000),
  CONSTRAINT `ck_shot_scripts_row_version` CHECK (`row_version` > 0),
  CONSTRAINT `ck_shot_scripts_image_settings` CHECK (`image_settings` IS NULL OR JSON_TYPE(`image_settings`) = 'OBJECT'),
  CONSTRAINT `ck_shot_scripts_deleted_time` CHECK (`deleted_at` IS NULL OR `created_at` IS NULL OR `deleted_at` >= `created_at`),
  CONSTRAINT `ck_shot_scripts_creation` CHECK (
    (`creation_key` IS NULL AND `creation_hash` IS NULL)
    OR (`creation_key` IS NOT NULL AND CHAR_LENGTH(TRIM(`creation_key`)) > 0
      AND `creation_hash` IS NOT NULL AND CHAR_LENGTH(`creation_hash`) = 64)
  ),
  CONSTRAINT `ck_shot_scripts_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='分镜脚本';

CREATE TABLE `shot_assets` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `episode_id` BIGINT UNSIGNED NOT NULL COMMENT '必须等于镜头所属分集',
  `asset_id` BIGINT UNSIGNED NOT NULL COMMENT '镜头引用的素材',
  `shot_id` BIGINT UNSIGNED NOT NULL COMMENT '所属镜头',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_shot_assets_asset` (`shot_id`, `asset_id`),
  KEY `idx_shot_assets_episode` (`episode_id`, `shot_id`),
  KEY `idx_shot_assets_asset_id` (`asset_id`),
  KEY `idx_shot_assets_shot_id_episode_id` (`shot_id`, `episode_id`),
  CONSTRAINT `fk_shot_assets_asset_id` FOREIGN KEY (`asset_id`)
    REFERENCES `assets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shot_assets_shot_episode` FOREIGN KEY (`shot_id`, `episode_id`)
    REFERENCES `shot_scripts` (`id`, `episode_id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_shot_assets_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='分镜素材关联';

CREATE TABLE `asset_image_candidates` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `asset_id` BIGINT UNSIGNED NOT NULL COMMENT '角色、场景或道具本体',
  `media_id` BIGINT UNSIGNED NOT NULL COMMENT '永久媒体文件',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_asset_image_candidates_media` (`asset_id`, `media_id`),
  KEY `idx_asset_image_candidates_time` (`asset_id`, `created_at`, `id`),
  KEY `idx_asset_image_candidates_media` (`media_id`),
  CONSTRAINT `fk_asset_image_candidates_asset` FOREIGN KEY (`asset_id`)
    REFERENCES `assets` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_asset_image_candidates_media` FOREIGN KEY (`media_id`)
    REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='素材参考图片候选';

CREATE TABLE `shot_images` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `episode_id` BIGINT UNSIGNED NOT NULL COMMENT '所属分集，与镜头一致',
  `shot_id` BIGINT UNSIGNED NOT NULL COMMENT '所属镜头',
  `layout` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'single' COMMENT '单图 / 四格 / 五格 / 九格',
  `aspect` VARCHAR(8) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '具体画幅：16:9、9:16、1:1、4:3或3:4',
  `resolution` VARCHAR(32) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT '2K' COMMENT '请求清晰度，如1K、2K、4K',
  `prompt` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '本次生图提示词',
  `media_id` BIGINT UNSIGNED NOT NULL COMMENT '用户确认采用的图片，不能为空',
  `state` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'confirmed' COMMENT '已确认；沿用指定字段，不表示生成任务状态',
  `model_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '生图配置；手动导入时可空',
  `context_hash` CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL COMMENT '采用时的shot-context-v1摘要',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_shot_images_shot` (`shot_id`),
  KEY `idx_shot_images_episode` (`episode_id`, `shot_id`),
  KEY `idx_shot_images_media_id` (`media_id`),
  KEY `idx_shot_images_model_id` (`model_id`),
  KEY `idx_shot_images_shot_id_episode_id` (`shot_id`, `episode_id`),
  CONSTRAINT `fk_shot_images_media_id` FOREIGN KEY (`media_id`)
    REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shot_images_model_id` FOREIGN KEY (`model_id`)
    REFERENCES `ai_model_configs` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shot_images_shot_episode` FOREIGN KEY (`shot_id`, `episode_id`)
    REFERENCES `shot_scripts` (`id`, `episode_id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_shot_images_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_shot_images_state` CHECK (`state` = 'confirmed'),
  CONSTRAINT `ck_shot_images_resolution` CHECK (CHAR_LENGTH(TRIM(`resolution`)) > 0),
  CONSTRAINT `ck_shot_images_layout` CHECK (`layout` IN ('single', 'four', 'five', 'nine')),
  CONSTRAINT `ck_shot_images_aspect` CHECK (`aspect` IN ('16:9', '9:16', '1:1', '4:3', '3:4')),
  CONSTRAINT `ck_shot_images_context_hash` CHECK (`context_hash` IS NULL OR CHAR_LENGTH(`context_hash`) = 64)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='分镜图';

CREATE TABLE `shot_videos` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `episode_id` BIGINT UNSIGNED NOT NULL COMMENT '所属分集，与镜头一致',
  `shot_id` BIGINT UNSIGNED NOT NULL COMMENT '所属镜头',
  `resolution` VARCHAR(32) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT '1080p' COMMENT '请求清晰度，如720p、1080p',
  `duration` BIGINT UNSIGNED NOT NULL COMMENT '请求视频时长，统一单位毫秒',
  `prompt` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '本次生视频提示词',
  `media_id` BIGINT UNSIGNED NOT NULL COMMENT '用户确认采用的视频，不能为空',
  `state` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'confirmed' COMMENT '已确认；沿用指定字段，不表示生成任务状态',
  `model_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '生视频配置；手动导入时可空',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `updated_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近修改时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  `updated_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '最近修改人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_shot_videos_shot` (`shot_id`),
  KEY `idx_shot_videos_episode` (`episode_id`, `shot_id`),
  KEY `idx_shot_videos_media_id` (`media_id`),
  KEY `idx_shot_videos_model_id` (`model_id`),
  KEY `idx_shot_videos_shot_id_episode_id` (`shot_id`, `episode_id`),
  CONSTRAINT `fk_shot_videos_media_id` FOREIGN KEY (`media_id`)
    REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shot_videos_model_id` FOREIGN KEY (`model_id`)
    REFERENCES `ai_model_configs` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shot_videos_shot_episode` FOREIGN KEY (`shot_id`, `episode_id`)
    REFERENCES `shot_scripts` (`id`, `episode_id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_shot_videos_audit_time` CHECK (`created_at` IS NULL OR `updated_at` IS NULL OR `updated_at` >= `created_at`),
  CONSTRAINT `ck_shot_videos_state` CHECK (`state` = 'confirmed'),
  CONSTRAINT `ck_shot_videos_resolution` CHECK (CHAR_LENGTH(TRIM(`resolution`)) > 0),
  CONSTRAINT `ck_shot_videos_duration` CHECK (`duration` > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='分镜视频';

CREATE TABLE `script_shot_records` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的记录标识',
  `script_id` BIGINT UNSIGNED NOT NULL COMMENT '作为生成来源的剧本',
  `shot_id` BIGINT UNSIGNED NOT NULL COMMENT '生成得到的分镜',
  `batch_id` BIGINT UNSIGNED NOT NULL COMMENT '一次生成的批次标识；不是外键',
  `model_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '本次使用的文本模型配置；演示数据可空',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '创建人；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_script_shot_batch_shot` (`batch_id`, `shot_id`),
  KEY `idx_script_shot_source` (`script_id`, `batch_id`),
  KEY `idx_script_shot_records_shot_id` (`shot_id`),
  KEY `idx_script_shot_records_model_id` (`model_id`),
  CONSTRAINT `fk_script_shot_records_script_id` FOREIGN KEY (`script_id`)
    REFERENCES `episode_scripts` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_script_shot_records_shot_id` FOREIGN KEY (`shot_id`)
    REFERENCES `shot_scripts` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_script_shot_records_model_id` FOREIGN KEY (`model_id`)
    REFERENCES `ai_model_configs` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_script_shot_records_batch` CHECK (`batch_id` > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='剧本生成分镜脚本记录';

CREATE TABLE `media_recycle_bin` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；本次进入回收站的记录标识',
  `shot_id` BIGINT UNSIGNED NOT NULL COMMENT '来源镜头；分集、项目通过镜头联查',
  `media_id` BIGINT UNSIGNED NOT NULL COMMENT '被废弃的图片或视频；进入回收站不删除文件',
  `model_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '生成该结果时使用的模型配置；手动导入或未知时可空',
  `prompt` MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '生成该结果时的提示词',
  `resolution` VARCHAR(32) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '生成该结果时的请求清晰度',
  `layout` VARCHAR(16) COLLATE utf8mb4_0900_bin NULL DEFAULT NULL COMMENT '图片布局：single / four / five / nine；视频为空',
  `aspect` VARCHAR(8) COLLATE utf8mb4_0900_bin NULL DEFAULT NULL COMMENT '图片请求画幅：16:9、9:16、1:1、4:3或3:4；视频为空',
  `duration` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '视频请求时长，单位毫秒；图片为空',
  `reason` VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL COMMENT '用户废弃 / 被新确认结果替换',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '本次进入回收站的时间，不是媒体生成时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '执行废弃或确认替换的用户；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_recycle_shot_media` (`shot_id`, `media_id`),
  KEY `idx_recycle_created` (`created_at`, `id`),
  KEY `idx_media_recycle_bin_media_id` (`media_id`),
  KEY `idx_media_recycle_bin_model_id` (`model_id`),
  CONSTRAINT `fk_media_recycle_bin_shot_id` FOREIGN KEY (`shot_id`)
    REFERENCES `shot_scripts` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_media_recycle_bin_media_id` FOREIGN KEY (`media_id`)
    REFERENCES `media_files` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_media_recycle_bin_model_id` FOREIGN KEY (`model_id`)
    REFERENCES `ai_model_configs` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_recycle_reason` CHECK (`reason` IN ('discarded', 'replaced')),
  CONSTRAINT `ck_recycle_resolution` CHECK (CHAR_LENGTH(TRIM(`resolution`)) > 0),
  CONSTRAINT `ck_recycle_layout` CHECK (`layout` IS NULL OR `layout` IN ('single', 'four', 'five', 'nine')),
  CONSTRAINT `ck_recycle_aspect` CHECK (`aspect` IS NULL OR `aspect` IN ('16:9', '9:16', '1:1', '4:3', '3:4')),
  CONSTRAINT `ck_recycle_duration` CHECK (`duration` IS NULL OR `duration` > 0),
  CONSTRAINT `ck_recycle_parameter_shape` CHECK ((`layout` IS NOT NULL AND `aspect` IS NOT NULL AND `duration` IS NULL) OR (`layout` IS NULL AND `aspect` IS NULL AND `duration` IS NOT NULL))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='分镜媒体回收站';

CREATE TABLE `novel_script_records` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花算法生成；稳定且不可变的生成记录标识',
  `novel_id` BIGINT UNSIGNED NOT NULL COMMENT '作为生成来源的分集小说',
  `script_id` BIGINT UNSIGNED NOT NULL COMMENT '本次生成得到的剧本；一份剧本最多一条生成来源记录',
  `batch_id` BIGINT UNSIGNED NOT NULL COMMENT '一次小说生成剧本操作的批次标识；不是外键',
  `model_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '本次使用的文本模型配置；演示或来源未知时可空',
  `created_at` DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '生成结果入库时间；正常写入非空，历史未知可显式NULL',
  `created_by` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '发起本次生成的用户；预留用户ID，暂不设外键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_novel_script_result` (`script_id`),
  KEY `idx_novel_script_source` (`novel_id`, `batch_id`),
  KEY `idx_novel_script_batch` (`batch_id`),
  KEY `idx_novel_script_records_model_id` (`model_id`),
  CONSTRAINT `fk_novel_script_records_novel_id` FOREIGN KEY (`novel_id`)
    REFERENCES `episode_novels` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_novel_script_records_script_id` FOREIGN KEY (`script_id`)
    REFERENCES `episode_scripts` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_novel_script_records_model_id` FOREIGN KEY (`model_id`)
    REFERENCES `ai_model_configs` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `ck_novel_script_records_batch` CHECK (`batch_id` > 0)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='小说生成剧本记录';

CREATE TABLE async_tasks (
  id BIGINT UNSIGNED NOT NULL,
  service_type VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL,
  status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'queued',
  idempotency_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL,
  request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  retry_of_id BIGINT UNSIGNED NULL,
  next_action VARCHAR(16) COLLATE utf8mb4_0900_bin NULL,
  next_run_at DATETIME(6) NULL,
  message_status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'pending',
  message_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
  publish_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
  lock_token VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  locked_until DATETIME(6) NULL,
  cancel_requested TINYINT UNSIGNED NOT NULL DEFAULT 0,
  error JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_async_tasks_idempotency (idempotency_key),
  KEY idx_async_tasks_publish (message_status, next_run_at, id),
  KEY idx_async_tasks_lock (message_status, locked_until, id),
  KEY idx_async_tasks_history (service_type, status, created_at, id),
  KEY idx_async_tasks_retry (retry_of_id),
  CONSTRAINT fk_async_tasks_retry FOREIGN KEY (retry_of_id)
    REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_async_tasks_type CHECK (service_type IN ('text','image','video')),
  CONSTRAINT ck_async_tasks_status CHECK (
    status IN ('queued','running','succeeded','failed','cancelled')
  ),
  CONSTRAINT ck_async_tasks_action CHECK (
    next_action IS NULL OR next_action IN ('submit','poll','save')
  ),
  CONSTRAINT ck_async_tasks_message CHECK (
    message_status IN ('pending','publishing','published','idle')
  ),
  CONSTRAINT ck_async_tasks_required CHECK (
    CHAR_LENGTH(TRIM(idempotency_key)) > 0
    AND CHAR_LENGTH(request_hash) = 64 AND message_version > 0
  ),
  CONSTRAINT ck_async_tasks_retry_self CHECK (retry_of_id IS NULL OR retry_of_id <> id),
  CONSTRAINT ck_async_tasks_cancel CHECK (cancel_requested IN (0,1)),
  CONSTRAINT ck_async_tasks_lock_pair CHECK (
    (lock_token IS NULL AND locked_until IS NULL)
    OR (lock_token IS NOT NULL AND locked_until IS NOT NULL)
  ),
  CONSTRAINT ck_async_tasks_terminal_time CHECK (
    (status IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL)
    OR (status IN ('queued','running') AND finished_at IS NULL)
  ),
  CONSTRAINT ck_async_tasks_time CHECK (
    updated_at >= created_at
    AND (started_at IS NULL OR started_at >= created_at)
    AND (finished_at IS NULL OR finished_at >= created_at)
    AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)
  )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='模型生成任务及当前动作投递';

CREATE TABLE ai_generation_records (
  id BIGINT UNSIGNED NOT NULL,
  task_id BIGINT UNSIGNED NOT NULL,
  call_no INT UNSIGNED NOT NULL,
  config_id BIGINT UNSIGNED NOT NULL,
  config_snapshot JSON NOT NULL,
  request_data JSON NOT NULL,
  credential_cipher TEXT NULL,
  adapter VARCHAR(64) COLLATE utf8mb4_0900_bin NULL,
  provider_task_id VARCHAR(255) COLLATE utf8mb4_0900_bin NULL,
  status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'prepared',
  text_content MEDIUMTEXT NULL,
  response_data JSON NULL,
  error JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ai_records_call (task_id, call_no),
  KEY idx_ai_records_config_time (config_id, created_at, id),
  KEY idx_ai_records_provider_task (provider_task_id),
  CONSTRAINT fk_ai_records_task FOREIGN KEY (task_id)
    REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_ai_records_config FOREIGN KEY (config_id)
    REFERENCES ai_model_configs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_ai_records_call_no CHECK (call_no > 0),
  CONSTRAINT ck_ai_records_status CHECK (
    status IN ('prepared','sent','succeeded','failed','unknown')
  ),
  CONSTRAINT ck_ai_records_time CHECK (
    updated_at >= created_at
    AND (started_at IS NULL OR started_at >= created_at)
    AND (finished_at IS NULL OR finished_at >= created_at)
    AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)
  )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='模型调用记录与文本结果';

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
