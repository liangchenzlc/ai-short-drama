# MySQL 8 数据表设计

依据：[数据模型.md](./数据模型.md)及已实现的模型生成流程。本文件覆盖原有 17 个业务模型和新增的任务、调用记录、媒体资产三表，当前共 20 张表；不增加用户、工作流槽位或分集模型选择表。

建表脚本：[schema.mysql8.sql](./schema.mysql8.sql)，统一维护全部 20 张表的最终 CREATE TABLE 定义，已合并配置能力缓存、任务五状态及移除项目目标时长的变更。新库只执行此文件；连接需预先使用 utf8mb4、UTC 和严格 SQL 模式。历史迁移仅供旧库升级，不与全量建表叠加执行。

## 设计约定

| 项目 | 设计 |
| --- | --- |
| 数据库版本 | MySQL 8.0.21 及以上；使用已强制执行的 CHECK、表达式默认值和生成列，不适用于 MySQL 5.7 或早期忽略 CHECK 的版本 |
| 引擎与字符集 | InnoDB，ROW_FORMAT=DYNAMIC，utf8mb4，默认 utf8mb4_0900_ai_ci |
| 精确比较 | 模型编码、状态等机器值与文件定位值使用 utf8mb4_0900_bin；大小写及尾随空格参与比较 |
| 主键与引用 | id 为 BIGINT UNSIGNED，由应用通过公共雪花算法工具类生成；外键、预留用户 ID、batch_id 同为 BIGINT UNSIGNED。批次 ID 由应用分配，同次生成复用，不另建批次表 |
| 接口表示 | BIGINT ID 以十进制字符串传给 JavaScript，避免超过 Number 安全整数范围 |
| 时间 | DATETIME(6)，应用连接统一 UTC；创建时间默认 CURRENT_TIMESTAMP(6)。允许历史未知时间显式写 NULL；正常业务写入必须有时间 |
| 修改审计 | 不使用 ON UPDATE CURRENT_TIMESTAMP；应用在实际内容变化时同时填写 updated_at 和 updated_by，打开项目只更新 last_opened_at |
| 用户预留 | created_by、updated_by 可空，暂不设外键；不创建替代审计主体，后续接入用户模型 |
| 文本 | 小说、剧本、分镜脚本、描述、提示词使用 MEDIUMTEXT，空值默认 ('')；按 utf8mb4 字节占用，上限约16 MiB |
| 字符串长度 | 名称通常120或255字符，模型编码255字符，地址2048字符，文件定位值700字符；这些是本次物理长度边界，写入前校验，不截断保存 |
| 文件定位索引 | storage_locator 使用700字符完整唯一索引，utf8mb4 最多2800字节，低于 InnoDB 3072字节限制；不使用前缀唯一索引 |
| 布尔与枚举 | 布尔用 TINYINT UNSIGNED + CHECK；枚举用 VARCHAR + CHECK，不使用 MySQL ENUM |
| 删除与更新 | 所有外键 ON DELETE RESTRICT、ON UPDATE RESTRICT；不隐含级联删除。AI 配置沿用 is_deleted 软删除 |
| 技术生成列 | 仅增加 confirmed_episode_id、default_service_type 两列以实现条件唯一；均由数据库计算，应用不得写入，不是新增业务数据 |
| SQL 使用范围 | 独立脚本按外键依赖顺序提供17条 CREATE TABLE，面向已选定的空数据库；不包含 DROP、迁移、数据导入或生产执行操作 |

执行脚本前先选定目标数据库并启用严格 SQL 模式（至少 STRICT_TRANS_TABLES）。脚本开头设置连接字符集为 utf8mb4、时区为 UTC；应用的每条连接也应使用相同设置。

普通时间字段允许 NULL 是为兼容已约定的历史未知值，不代表正常创建可以缺少时间。用户字段为空时不能伪造操作人。密钥字段只保存应用层加密后的编码信封，加密主密钥不放数据库。

下列字段表、索引表与独立建表脚本一一对应。检查约束只验证本行；跨表业务规则集中列在文末，不宣称外键或 CHECK 能完成全部业务校验。

## 1. 项目 — projects

用途：保存短剧项目的基本信息和新建分集时使用的默认制作设置。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| name | VARCHAR(120) | 否 | 无，写入时必填 | 项目名称，允许重名 |
| synopsis | MEDIUMTEXT | 否 | ('') | 故事梗概 |
| style | VARCHAR(255) | 否 | '' | 新分集默认风格 |
| aspect | VARCHAR(8) | 否 | 无，写入时必填 | 新分集默认画幅 |
| last_opened_at | DATETIME(6) | 是 | NULL | 最近打开时间 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| INDEX idx_projects_last_opened | last_opened_at, id | 按最近打开时间分页 |
| CHECK ck_projects_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_projects_name | 见建表脚本 | 名称非空 |
| CHECK ck_projects_aspect | 见建表脚本 | 项目画幅 |

## 2. AI模型配置 — ai_model_configs

用途：保存文本、生图、生视频的配置、密钥及启用状态。每条配置对应一个具体模型，以 model_key 保存供应商模型编码。原 ai_configs 统一更名为本表，删除 ai_models 后相关 model_id 引用本表 id，调用时读取对应的 model_key。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| service_type | VARCHAR(16) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 文本 / 生图 / 生视频 |
| name | VARCHAR(120) | 否 | 无，写入时必填 | 配置显示名 |
| model_key | VARCHAR(255) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 供应商模型编码，保留大小写；每条配置仅保存一个模型编码 |
| provider | VARCHAR(120) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 供应商名称 |
| base_url | VARCHAR(2048) | 否 | '' | 服务基础地址 |
| apikey | TEXT | 是 | NULL | 加密密钥信封（含算法/密钥版本/随机数/认证标签/密文的编码串），不存明文 |
| capability_cache | JSON | 是 | NULL | 系统内部协议与能力缓存，不由用户编辑 |
| enabled | TINYINT UNSIGNED | 否 | 1 | 是否允许用于新操作 |
| is_deleted | TINYINT UNSIGNED | 否 | 0 | 是否逻辑删除 |
| is_default | TINYINT UNSIGNED | 否 | 0 | 是否本类型默认配置 |
| row_version | BIGINT UNSIGNED | 否 | 1 | 沿用旧表；是否与其他表一并删除见文末 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |
| default_service_type | VARCHAR(16) COLLATE utf8mb4_0900_bin | 是 | 数据库生成（见建表脚本） | 技术生成列：用于每类最多一个未删除默认配置；应用不写入 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_ai_default_service | default_service_type | 非默认/已删除记录为NULL，可有多条 |
| INDEX idx_ai_type_available | service_type, is_deleted, enabled | 按类型查询可用配置 |
| CHECK ck_ai_model_configs_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_ai_service_type | 见建表脚本 | 限定配置类型 |
| CHECK ck_ai_required_text | 见建表脚本 | 显示名、模型编码和供应商非空 |
| CHECK ck_ai_flags | 见建表脚本 | 布尔字段只能为0或1 |
| CHECK ck_ai_default_available | 见建表脚本 | 默认配置必须启用且未删除 |
| CHECK ck_ai_version | 见建表脚本 | 沿用现有并发版本 |

## 3. 媒体元数据 — media_files

用途：保存图片、视频文件的位置和实际元数据，由素材、分镜图和分镜视频直接引用。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| format_code | VARCHAR(127) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 直接保存规范 MIME，例如 image/png、video/mp4；演示资源可用 demo:image |
| storage_locator | VARCHAR(700) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 稳定存储定位值，不保存临时签名 URL 或 Blob URL |
| original_name | VARCHAR(255) | 否 | '' | 原始文件名 |
| byte_size | BIGINT UNSIGNED | 是 | NULL | 实际文件字节数 |
| width | INT UNSIGNED | 是 | NULL | 实际像素宽度 |
| height | INT UNSIGNED | 是 | NULL | 实际像素高度 |
| duration_ms | BIGINT UNSIGNED | 是 | NULL | 实际视频时长，单位毫秒；图片为空 |
| checksum_sha256 | CHAR(64) CHARACTER SET ascii COLLATE ascii_bin | 是 | NULL | 文件校验值，不作为文件身份 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_media_locator | storage_locator | 精确比较完整稳定定位值，非前缀索引 |
| CHECK ck_media_files_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_media_locator | 见建表脚本 | 定位值非空 |
| CHECK ck_media_format | 见建表脚本 | 只支持图片和视频；真实格式由应用校验 |
| CHECK ck_media_dimensions | 见建表脚本 | 已知尺寸为正数 |
| CHECK ck_media_duration | 见建表脚本 | 时长为正且仅视频可填 |
| CHECK ck_media_checksum | 见建表脚本 | SHA-256长度；十六进制内容由应用校验 |

## 4. 分集 — episodes

用途：保存分集归属、排序、标题及制作设置；小说和剧本通过各自的 episode_id 联查。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| project_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属项目 |
| position | INT UNSIGNED | 否 | 无，写入时必填 | 项目内分集排序 |
| title | VARCHAR(255) | 否 | 无，写入时必填 | 分集标题 |
| synopsis | MEDIUMTEXT | 否 | ('') | 分集简介 |
| aspect | VARCHAR(8) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 本集画幅 |
| style | VARCHAR(255) | 否 | '' | 本集风格 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_episodes_project_position | project_id, position | 项目内排序唯一；覆盖分集列表查询 |
| FOREIGN KEY fk_episodes_project_id | (project_id) → projects(id) | 限制删除与修改被引用键 |
| CHECK ck_episodes_position | 见建表脚本 | 排序为正整数 |
| CHECK ck_episodes_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_episodes_title | 见建表脚本 | 标题非空 |
| CHECK ck_episodes_aspect | 见建表脚本 | 分集画幅 |

## 5. 小说原文 — episode_novels

用途：保存用户在分集详情中输入、粘贴并保存的小说正文。通过 episode_id 关联分集，作为生成剧本的来源；小说与剧本分别保存，不在分集表重复存储正文。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| episode_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属分集 |
| content | MEDIUMTEXT | 否 | ('') | 分集详情中用户输入的当前小说正文 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_novels_episode | episode_id | 每集最多一份小说 |
| FOREIGN KEY fk_episode_novels_episode_id | (episode_id) → episodes(id) | 限制删除与修改被引用键 |
| CHECK ck_episode_novels_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |

## 6. 剧情（剧本） — episode_scripts

用途：合并原剧本候选与最近剧本确认，直接保存剧本正文和是否确认的状态；后文“剧本 ID”均指本表 id。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| episode_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属分集 |
| position | INT UNSIGNED | 否 | 无，写入时必填 | 同一分集内剧本排序，沿用原候选排序 |
| content | MEDIUMTEXT | 否 | ('') | 剧本正文 |
| state | VARCHAR(16) COLLATE utf8mb4_0900_bin | 否 | 'unconfirmed' | 未确认 / 已确认 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |
| confirmed_episode_id | BIGINT UNSIGNED | 是 | 数据库生成（见建表脚本） | 技术生成列：已确认时为分集ID，否则NULL；应用不写入 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_scripts_episode_position | episode_id, position | 每集剧本排序唯一 |
| UNIQUE uk_scripts_confirmed_episode | confirmed_episode_id | 每集最多一份已确认剧本 |
| FOREIGN KEY fk_episode_scripts_episode_id | (episode_id) → episodes(id) | 限制删除与修改被引用键 |
| CHECK ck_episode_scripts_position | 见建表脚本 | 排序为正整数 |
| CHECK ck_episode_scripts_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_scripts_state | 见建表脚本 | 剧本确认状态 |

## 7. 素材 — assets

用途：保存与项目、分集归属解耦的素材本体。全局库、项目库、分集库仅保存对本表的引用。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| kind | VARCHAR(16) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 角色 / 场景 / 道具 |
| name | VARCHAR(255) | 否 | 无，写入时必填 | 素材名称，允许重名 |
| label | VARCHAR(120) | 否 | '' | 单个标签或分类文本，不存逗号分隔的多标签 |
| description | MEDIUMTEXT | 否 | ('') | 素材描述 |
| prompt | MEDIUMTEXT | 否 | ('') | 素材生成提示词 |
| model_id | BIGINT UNSIGNED | 是 | NULL | 使用的模型配置；手动建立、导入时可空 |
| media_id | BIGINT UNSIGNED | 是 | NULL | 当前素材图片，尚未生成时可空 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| INDEX idx_assets_kind_name | kind, name | 按类型筛选与名称前缀查找 |
| INDEX idx_assets_model_id | model_id | 外键索引及反向引用查询 |
| INDEX idx_assets_media_id | media_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_assets_model_id | (model_id) → ai_model_configs(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_assets_media_id | (media_id) → media_files(id) | 限制删除与修改被引用键 |
| CHECK ck_assets_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_assets_kind | 见建表脚本 | 素材类型 |
| CHECK ck_assets_name | 见建表脚本 | 素材名称非空 |

## 8. 全部素材（全局库关联） — global_assets

用途：登记素材是否加入全局素材库及其排序；这里把“全部素材”解释为可独立管理的全局库。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| asset_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 素材本体 |
| position | INT UNSIGNED | 否 | 无，写入时必填 | 全局库排序 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_global_assets_asset | asset_id | 同一素材只登记一次 |
| UNIQUE uk_global_assets_position | position | 全局库排序 |
| FOREIGN KEY fk_global_assets_asset_id | (asset_id) → assets(id) | 限制删除与修改被引用键 |
| CHECK ck_global_assets_position | 见建表脚本 | 排序为正整数 |

## 9. 项目素材关联 — project_assets

用途：登记某个项目收录哪些素材及显示顺序。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| project_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 项目 |
| asset_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 素材本体 |
| position | INT UNSIGNED | 否 | 无，写入时必填 | 项目内排序 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_project_assets_asset | project_id, asset_id | 同一归属内素材不重复 |
| UNIQUE uk_project_assets_position | project_id, position | 归属内排序唯一 |
| INDEX idx_project_assets_asset_id | asset_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_project_assets_project_id | (project_id) → projects(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_project_assets_asset_id | (asset_id) → assets(id) | 限制删除与修改被引用键 |
| CHECK ck_project_assets_position | 见建表脚本 | 排序为正整数 |

## 10. 分集素材关联 — episode_assets

用途：登记某个分集使用哪些素材及显示顺序。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| episode_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 分集 |
| asset_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 素材本体 |
| position | INT UNSIGNED | 否 | 无，写入时必填 | 分集内排序 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_episode_assets_asset | episode_id, asset_id | 同一归属内素材不重复 |
| UNIQUE uk_episode_assets_position | episode_id, position | 归属内排序唯一 |
| INDEX idx_episode_assets_asset_id | asset_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_episode_assets_episode_id | (episode_id) → episodes(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_episode_assets_asset_id | (asset_id) → assets(id) | 限制删除与修改被引用键 |
| CHECK ck_episode_assets_position | 见建表脚本 | 排序为正整数 |

## 11. 分镜脚本 — shot_scripts

用途：保存每个镜头的分镜脚本及分集内顺序；界面按顺序显示“分镜镜头1、2……”而不保存标题。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| episode_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属分集 |
| position | INT UNSIGNED | 否 | 无，写入时必填 | 分集内镜头顺序 |
| script | MEDIUMTEXT | 否 | ('') | 分镜脚本正文 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_shots_episode_position | episode_id, position | 镜头排序；用于分集镜头列表 |
| UNIQUE uk_shots_id_episode | id, episode_id | 供分镜子表复合外键引用 |
| FOREIGN KEY fk_shot_scripts_episode_id | (episode_id) → episodes(id) | 限制删除与修改被引用键 |
| CHECK ck_shot_scripts_position | 见建表脚本 | 排序为正整数 |
| CHECK ck_shot_scripts_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |

## 12. 分镜素材关联 — shot_assets

用途：记录某个镜头引用哪些素材，直接引用统一素材本体。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| episode_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 必须等于镜头所属分集 |
| asset_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 镜头引用的素材 |
| shot_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属镜头 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_shot_assets_asset | shot_id, asset_id | 同一镜头不重复引用素材 |
| INDEX idx_shot_assets_episode | episode_id, shot_id | 按分集读取镜头素材 |
| INDEX idx_shot_assets_asset_id | asset_id | 外键索引及反向引用查询 |
| INDEX idx_shot_assets_shot_id_episode_id | shot_id, episode_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_shot_assets_asset_id | (asset_id) → assets(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_shot_assets_shot_episode | (shot_id, episode_id) → shot_scripts(id, episode_id) | 限制删除与修改被引用键 |
| CHECK ck_shot_assets_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |

## 13. 分镜图 — shot_images

用途：只保存用户为镜头确认采用的那一份图片及其生成参数；未确认结果不写入本表，废弃结果进入 media_recycle_bin。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| episode_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属分集，与镜头一致 |
| shot_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属镜头 |
| layout | VARCHAR(16) COLLATE utf8mb4_0900_bin | 否 | 'single' | 单图 / 四格 / 五格 / 九格 |
| aspect | VARCHAR(8) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 具体画幅：16:9、9:16、1:1、4:3或3:4 |
| resolution | VARCHAR(32) COLLATE utf8mb4_0900_bin | 否 | '2K' | 请求清晰度，如1K、2K、4K |
| prompt | MEDIUMTEXT | 否 | ('') | 本次生图提示词 |
| media_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 用户确认采用的图片，不能为空 |
| state | VARCHAR(16) COLLATE utf8mb4_0900_bin | 否 | 'confirmed' | 已确认；沿用指定字段，不表示生成任务状态 |
| model_id | BIGINT UNSIGNED | 是 | NULL | 生图配置；手动导入时可空 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_shot_images_shot | shot_id | 每镜最多一个确认结果 |
| INDEX idx_shot_images_episode | episode_id, shot_id | 按分集读取确认结果 |
| INDEX idx_shot_images_media_id | media_id | 外键索引及反向引用查询 |
| INDEX idx_shot_images_model_id | model_id | 外键索引及反向引用查询 |
| INDEX idx_shot_images_shot_id_episode_id | shot_id, episode_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_shot_images_media_id | (media_id) → media_files(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_shot_images_model_id | (model_id) → ai_model_configs(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_shot_images_shot_episode | (shot_id, episode_id) → shot_scripts(id, episode_id) | 限制删除与修改被引用键 |
| CHECK ck_shot_images_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_shot_images_state | 见建表脚本 | 正式表只保存确认结果 |
| CHECK ck_shot_images_resolution | 见建表脚本 | 请求清晰度非空，能力由供应商适配器校验 |
| CHECK ck_shot_images_layout | 见建表脚本 | 图片布局 |
| CHECK ck_shot_images_aspect | 见建表脚本 | 图片具体画幅 |

## 14. 分镜视频 — shot_videos

用途：只保存用户为镜头确认采用的那一份视频及其生成参数；未确认结果不写入本表，废弃结果进入 media_recycle_bin。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| episode_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属分集，与镜头一致 |
| shot_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 所属镜头 |
| resolution | VARCHAR(32) COLLATE utf8mb4_0900_bin | 否 | '1080p' | 请求清晰度，如720p、1080p |
| duration | BIGINT UNSIGNED | 否 | 无，写入时必填 | 请求视频时长，统一单位毫秒 |
| prompt | MEDIUMTEXT | 否 | ('') | 本次生视频提示词 |
| media_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 用户确认采用的视频，不能为空 |
| state | VARCHAR(16) COLLATE utf8mb4_0900_bin | 否 | 'confirmed' | 已确认；沿用指定字段，不表示生成任务状态 |
| model_id | BIGINT UNSIGNED | 是 | NULL | 生视频配置；手动导入时可空 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| updated_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 最近修改时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |
| updated_by | BIGINT UNSIGNED | 是 | NULL | 最近修改人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_shot_videos_shot | shot_id | 每镜最多一个确认结果 |
| INDEX idx_shot_videos_episode | episode_id, shot_id | 按分集读取确认结果 |
| INDEX idx_shot_videos_media_id | media_id | 外键索引及反向引用查询 |
| INDEX idx_shot_videos_model_id | model_id | 外键索引及反向引用查询 |
| INDEX idx_shot_videos_shot_id_episode_id | shot_id, episode_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_shot_videos_media_id | (media_id) → media_files(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_shot_videos_model_id | (model_id) → ai_model_configs(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_shot_videos_shot_episode | (shot_id, episode_id) → shot_scripts(id, episode_id) | 限制删除与修改被引用键 |
| CHECK ck_shot_videos_audit_time | 见建表脚本 | 时间均已知时，修改时间不得早于创建时间 |
| CHECK ck_shot_videos_state | 见建表脚本 | 正式表只保存确认结果 |
| CHECK ck_shot_videos_resolution | 见建表脚本 | 请求清晰度非空，能力由供应商适配器校验 |
| CHECK ck_shot_videos_duration | 见建表脚本 | 视频请求时长为正 |

## 15. 剧本生成分镜脚本记录 — script_shot_records

用途：记录某份剧本通过一次生成操作产出了哪些分镜。每行对应一个产出的分镜；同一批次的多个镜头使用相同 batch_id。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的记录标识 |
| script_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 作为生成来源的剧本 |
| shot_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 生成得到的分镜 |
| batch_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 一次生成的批次标识；不是外键 |
| model_id | BIGINT UNSIGNED | 是 | NULL | 本次使用的文本模型配置；演示数据可空 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 创建时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 创建人；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_script_shot_batch_shot | batch_id, shot_id | 批次内镜头不重复 |
| INDEX idx_script_shot_source | script_id, batch_id | 查询剧本的生成批次及产出 |
| INDEX idx_script_shot_records_shot_id | shot_id | 外键索引及反向引用查询 |
| INDEX idx_script_shot_records_model_id | model_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_script_shot_records_script_id | (script_id) → episode_scripts(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_script_shot_records_shot_id | (shot_id) → shot_scripts(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_script_shot_records_model_id | (model_id) → ai_model_configs(id) | 限制删除与修改被引用键 |
| CHECK ck_script_shot_records_batch | 见建表脚本 | 应用分配正整数批次ID，同次操作复用 |

## 16. 分镜媒体回收站 — media_recycle_bin

用途：保存用户明确废弃的分镜图片、视频，以及确认新结果后被替换的旧结果。保留文件引用和生成参数，支持恢复；仅包含仍在回收站中的记录，不承担生成任务或完整操作日志功能。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 本次进入回收站的记录标识 |
| shot_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 来源镜头；分集、项目通过镜头联查 |
| media_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 被废弃的图片或视频；进入回收站不删除文件 |
| model_id | BIGINT UNSIGNED | 是 | NULL | 生成该结果时使用的模型配置；手动导入或未知时可空 |
| prompt | MEDIUMTEXT | 否 | ('') | 生成该结果时的提示词 |
| resolution | VARCHAR(32) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 生成该结果时的请求清晰度 |
| layout | VARCHAR(16) COLLATE utf8mb4_0900_bin | 是 | NULL | 图片布局：single / four / five / nine；视频为空 |
| aspect | VARCHAR(8) COLLATE utf8mb4_0900_bin | 是 | NULL | 图片请求画幅：16:9、9:16、1:1、4:3或3:4；视频为空 |
| duration | BIGINT UNSIGNED | 是 | NULL | 视频请求时长，单位毫秒；图片为空 |
| reason | VARCHAR(16) COLLATE utf8mb4_0900_bin | 否 | 无，写入时必填 | 用户废弃 / 被新确认结果替换 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 本次进入回收站的时间，不是媒体生成时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 执行废弃或确认替换的用户；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_recycle_shot_media | shot_id, media_id | 同镜头同媒体最多一条回收记录 |
| INDEX idx_recycle_created | created_at, id | 回收时间分页 |
| INDEX idx_media_recycle_bin_media_id | media_id | 外键索引及反向引用查询 |
| INDEX idx_media_recycle_bin_model_id | model_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_media_recycle_bin_shot_id | (shot_id) → shot_scripts(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_media_recycle_bin_media_id | (media_id) → media_files(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_media_recycle_bin_model_id | (model_id) → ai_model_configs(id) | 限制删除与修改被引用键 |
| CHECK ck_recycle_reason | 见建表脚本 | 入站原因 |
| CHECK ck_recycle_resolution | 见建表脚本 | 请求清晰度非空 |
| CHECK ck_recycle_layout | 见建表脚本 | 图片布局值 |
| CHECK ck_recycle_aspect | 见建表脚本 | 图片画幅值 |
| CHECK ck_recycle_duration | 见建表脚本 | 视频时长值 |
| CHECK ck_recycle_parameter_shape | 见建表脚本 | 图片/视频参数二选一；与实际媒体类型匹配仍需应用校验 |

## 17. 小说生成剧本记录 — novel_script_records

用途：记录分集小说通过哪次生成操作产出了哪些剧本，并保存使用的文本模型配置。每行对应一份产出的剧本；同次生成的多份剧本共享 batch_id，与剧本生成分镜记录采用相同组织方式。

| 字段 | MySQL 类型 | 可空 | 默认值 / 生成方式 | 说明 |
| --- | --- | --- | --- | --- |
| id | BIGINT UNSIGNED | 否 | 应用雪花算法生成 | 稳定且不可变的生成记录标识 |
| novel_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 作为生成来源的分集小说 |
| script_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 本次生成得到的剧本；一份剧本最多一条生成来源记录 |
| batch_id | BIGINT UNSIGNED | 否 | 无，写入时必填 | 一次小说生成剧本操作的批次标识；不是外键 |
| model_id | BIGINT UNSIGNED | 是 | NULL | 本次使用的文本模型配置；演示或来源未知时可空 |
| created_at | DATETIME(6) | 是 | CURRENT_TIMESTAMP(6) | 生成结果入库时间；正常写入非空，历史未知可显式NULL |
| created_by | BIGINT UNSIGNED | 是 | NULL | 发起本次生成的用户；预留用户ID，暂不设外键 |

| 索引 / 约束 | 字段或规则 | 用途 |
| --- | --- | --- |
| PRIMARY KEY | id | 聚簇主键 |
| UNIQUE uk_novel_script_result | script_id | 每份剧本最多一份生成来源 |
| INDEX idx_novel_script_source | novel_id, batch_id | 查询小说产出的剧本 |
| INDEX idx_novel_script_batch | batch_id | 查询同次生成结果 |
| INDEX idx_novel_script_records_model_id | model_id | 外键索引及反向引用查询 |
| FOREIGN KEY fk_novel_script_records_novel_id | (novel_id) → episode_novels(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_novel_script_records_script_id | (script_id) → episode_scripts(id) | 限制删除与修改被引用键 |
| FOREIGN KEY fk_novel_script_records_model_id | (model_id) → ai_model_configs(id) | 限制删除与修改被引用键 |
| CHECK ck_novel_script_records_batch | 见建表脚本 | 应用分配正整数批次ID，同次操作复用 |

## 需要应用事务保证的规则

下表说明数据库约束的边界。所有写入入口必须遵守同一事务与锁规则；不能绕过服务直接改表后仍期待这些业务规则成立。

| 场景 | 数据库已经保证 | 应用需要保证 |
| --- | --- | --- |
| 每集确认一份剧本 | confirmed_episode_id 在 confirmed 时等于 episode_id，否则为 NULL；唯一索引允许多个 NULL，但拒绝同集两份确认 | 确认时先锁 episodes 对应行，校验目标剧本存在且属于该集，再将旧确认改为 unconfirmed、目标改为 confirmed，更新实际变化记录的审计后提交 |
| 编辑已确认剧本 | state 仅允许两种值，确认唯一性始终存在 | 在同一事务中锁定分集与剧本；正文按实际字符内容比较，内容变化时同时取消确认。不用默认不区分大小写的排序规则判断正文是否相同；仅重排或原样保存不取消确认 |
| 默认模型切换 | 每类最多一个未删除默认配置；默认必须启用且未删除 | 先取消旧默认再设置新默认，同事务提交。并发冲突由唯一约束兜底，遇死锁或唯一冲突回滚重试；不使用 REPLACE INTO 删除重插 |
| AI 配置修改 | row_version 为正整数，默认1 | 如使用并发控制，更新必须携带旧版本条件并递增，检查影响行数；只定义字段不会自动产生乐观锁。service_type 建立后不跨类型修改 |
| 选择生成模型 | model_id 必须引用存在的配置 | 新操作校验 enabled=1、is_deleted=0 及 text/image/video 类型；停用或删除不得阻断旧结果的展示、恢复和来源关联 |
| 图片与视频引用 | 媒体存在；正式表每个 shot_id 唯一且 state 固定为 confirmed | 图片表及素材只引用图片，视频表只引用视频；回收站参数形状必须与媒体格式匹配。媒体类型、稳定定位值一经引用不原地改作其他文件 |
| 确认、替换、恢复 | 正式图/视频每镜最多一条；回收站同镜同媒体最多一条 | 先锁 shot_scripts 镜头行，校验归属及媒体类型；把旧结果及参数入回收站，再写新确认结果，并移除恢复来源的回收记录，一次提交。镜头尚无正式结果时也锁父镜头行，防止并发首次确认 |
| 正式结果与回收互斥 | 两表各自唯一，但跨表互斥不能由 CHECK 表达 | 同一镜头同一 media_id 不同时出现在正式结果和回收站；确认、废弃、恢复共用镜头锁。相同媒体被其他镜头或素材使用不违反规则 |
| 清理回收站与文件 | 所有引用媒体的表都有 RESTRICT 外键，阻止删除仍被引用的媒体元数据 | 清理时锁定 media_files 行并重新检查全部引用；只清理不再引用的文件。物理删除跨越存储系统，需可重试、幂等并防止清理期间新增引用，不能仅依赖一次无锁查询；具体协调机制留待后端设计 |
| 生成来源同集 | 小说、剧本、分镜外键保证记录存在 | 两张生成记录表的来源与产出必须属于同一分集；创建后禁止改变这些实体的分集归属，以免破坏已有来源关系 |
| 批次一致性 | batch_id 查询索引及结果唯一约束 | 一次生成统一分配 batch_id、model_id、时间和操作人；同批来源一致，所有产出及来源记录一次事务写入。重试复用稳定批次与产出标识，不生成另一组重复剧本/分镜 |
| 生成记录不可变 | 外键阻止直接删除被引用的小说、剧本或分镜 | 来源记录和回收记录插入后不改写；不是 UPDATE 触发器保证。生成失败且无结果不写入这两张来源表 |
| 小说保存与生成 | 每集最多一份小说 | 无正文不能生成；生成得到的剧本初始 unconfirmed；修改小说不覆盖既有剧本。需要历史输入正文时另行确认快照方案 |
| 共享素材独立修改 | 三个库的关联唯一性与素材外键 | 复制新的 assets 记录并只替换指定关联，使用新的审计信息。素材媒体引用可共享，换图不覆盖原文件；镜头引用需显式替换 |
| 排序变更 | 各归属内 position 唯一且大于0 | MySQL 唯一检查不延迟到提交；交换排序前锁定归属行或相关集合，使用不冲突的正整数临时区间，再写最终顺序，全部在事务中完成 |
| 时间和操作人 | 非空时间不能逆序；用户ID目前无外键 | 正常创建填写时间；有修改审计的表只在实际变化时更新。创建信息与主键不可被普通更新覆盖；仅有创建审计的关系表重排不记录修改人 |

`confirmed_episode_id` 和 `default_service_type` 是 MySQL 缺少部分唯一索引时的实现方式，不恢复旧的确认表或模型表。更新剧本时自动取消确认由应用事务负责，本文件没有附加触发器。

回收站的图片专属字段与视频专属字段用本行 CHECK 保证二选一；是否匹配 media_files 的实际类型仍须联查校验，MySQL CHECK 不支持跨表查询。

## 范式与当前边界

- 保留逻辑文档已经说明的受控冗余：shot_assets.episode_id 用于同集外键校验；两张生成来源表将批次元数据与结果放在同表，减少表数量，代价是由事务保证批次一致。
- shot_images、shot_videos 的 shot_id 为候选键；各自的 episode_id 是跨表冗余，并通过复合外键保持一致。
- 两个生成列完全由基础字段计算，没有独立编辑或同步入口；它们只服务条件唯一索引。
- 保留 global_assets 作为独立收录的全局库，尚不改成所有 assets 的汇总查询。
- AI 配置的 row_version 沿用现有逻辑模型；是否删除仍待确认。项目、分集不重新增加版本字段。
- 不增加视频输入图片字段、输入快照或历史版本。生成记录能追溯来源 ID，不能还原后来被编辑过的历史正文。
- 生成任务、调用记录与生成媒体资产已纳入完整建表脚本，见下文补充；密钥加密仍在应用层，存储清理协调和用户系统未增加独立业务表。

## 核对与执行范围

当前全量建表入口为 `schema.mysql8.sql`，包含 20 张表，只有 CREATE TABLE 语句。
新增三表的字段、默认值、索引、检查约束与外键以该 SQL 为准：

| 表 | 用途与关键约束 |
| --- | --- |
| async_tasks | 模型生成任务及当前动作投递；idempotency_key 唯一；任务状态为 queued/running/succeeded/failed/cancelled；保留重试来源、消息版本、租约、取消标记与时间约束 |
| ai_generation_records | 调用配置及请求快照、供应商任务标识、文本结果与错误；(task_id, call_no) 唯一；关联任务和模型配置；内部调用状态仍保留 unknown 作为受理不明证据 |
| media_assets | 生成图片/视频资产；(record_id, output_index) 和 media_id 分别唯一；关联调用记录及媒体文件；保留名称与 row_version |

`ai_model_configs.capability_cache` 已直接包含在建表定义中，项目目标时长不再存在。
历史迁移脚本继续保留作为旧库升级记录，不参与新库初始化。

已按逻辑模型进行字段覆盖、外键目标、建表依赖、索引字段与关键唯一约束核对。2026-09-18 后端验收在用户指定云服务器的独立临时测试库中，使用 MySQL 8.4.11 执行完整建表脚本，并通过 CRUD、外键回滚、条件唯一、回收恢复、批次事务及并发确认测试。测试库执行后清理，现有 ai_short_drama 的业务数据和表结构未修改；现有自增属性与应用显式写入雪花 ID 兼容，新建表脚本已取消自增。其他 MySQL 版本部署时仍需运行集成测试。
