# MySQL 8 数据库说明

项目当前使用 **21 张表**。[schema.mysql8.sql](schema.mysql8.sql) 是新库初始化的完整结构，已经合并全部历史迁移，包括分集写作、素材图片候选、分镜归档与并发控制、镜头建议时长和原文依据。字段的完整类型、默认值、索引和 CHECK 以该文件为准；[SQLAlchemy Domain](../../backend/src/short_drama/domain) 与其保持一致。

本文说明表的职责、关系及应用维护的约束，不另维护一份重复的字段清单。开发与测试见[开发说明](../development.md)，模块关系见[架构说明](../architecture.md)，接口见[API 文档](../api/README.md)。旧库升级使用[迁移目录](migrations/README.md)。

## 初始化与升级

新库要求 MySQL **8.0.21+**。先由数据库管理员创建并选定空数据库，连接使用 `utf8mb4`、UTC（`+00:00`）及严格 SQL 模式（至少 `STRICT_TRANS_TABLES`），再执行完整的 `schema.mysql8.sql`。文件只包含按外键依赖顺序排列的 21 条 `CREATE TABLE`，不创建数据库、不设置连接、不导入数据，也不自动选择业务库。

已有数据库不能用全量脚本覆盖。根据实际字段、索引、约束和已执行记录判断缺少哪些迁移，按[迁移顺序](migrations/README.md)补齐；应用启动不会自动迁移。新库执行完整脚本后无需叠加历史迁移。

## 物理约定

| 项目 | 当前约定 |
| --- | --- |
| 存储 | InnoDB、`ROW_FORMAT=DYNAMIC`、`utf8mb4`，表默认排序规则 `utf8mb4_0900_ai_ci` |
| 精确值 | 状态、模型编码、幂等键、存储定位值等使用 `utf8mb4_0900_bin`；摘要使用 `ascii_bin` |
| 主键 | `BIGINT UNSIGNED`，由应用雪花算法生成，不使用 `AUTO_INCREMENT`；API 中 ID 以十进制字符串传递 |
| 时间 | `DATETIME(6)`，连接统一 UTC；数据库默认创建时间，应用维护修改时间，不使用 `ON UPDATE CURRENT_TIMESTAMP` |
| 历史审计 | 传统业务表允许未知审计时间为 NULL；任务、调用记录、生成媒体资产和素材候选的创建时间必须非空，前三者的修改时间也必须非空 |
| 用户预留 | `created_by`、`updated_by` 可空，无用户外键；不伪造未知操作人 |
| 正文 | 小说、剧本、分镜、描述和提示词使用 `MEDIUMTEXT`；大多数空正文默认 `('')` |
| 删除 | 所有外键使用 `ON DELETE RESTRICT ON UPDATE RESTRICT`，由应用显式维护关系；不会自动级联清理文件 |
| 枚举 | `VARCHAR` 配合 CHECK；标志使用 `TINYINT UNSIGNED` 配合 CHECK |
| 条件唯一 | 三个持久生成列 `default_service_type`、`confirmed_episode_id`、`active_position` 实现条件唯一，应用不写入这些列 |

普通索引支持列表筛选、顺序和关系查询；唯一索引维护默认配置、已确认剧本、活动镜头顺序及请求去重等不变量。CHECK 只能校验本行，跨表媒体类型、模型服务类型及业务归属仍由应用事务校验。

## 表与关系

### 项目与分集写作（4 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `projects` | 项目名称、梗概、默认 `style/aspect`、`last_opened_at` | 名称允许重名；画幅为 `16:9/9:16`；没有项目目标时长字段 |
| `episodes` | 分集标题、简介、自己的制作设置、`editing_script_id`、`content_version`、`storyboard_version` | 属于项目；`(project_id, position)` 唯一；两个版本默认 1 且为正数 |
| `episode_novels` | 分集当前小说正文 `content` | `episode_id` 唯一，每集最多一条小说 |
| `episode_scripts` | 多份剧本正文、排序、`state` | `(episode_id, position)` 唯一；状态为 `unconfirmed/confirmed`；生成列确保每集最多一份已确认剧本 |

`episodes.editing_script_id` 表示编辑器当前选择，与已确认剧本不是同一概念。该字段不设外键，避免分集和剧本的循环依赖；应用锁定分集后检查剧本属于本集，并在删除当前剧本时维护指针。`content_version` 用于小说、剧本与编辑选择的乐观并发控制，修改时检查客户端观察到的版本。

### 模型配置（1 张）

`ai_model_configs` 每行对应一种服务的一个具体模型。`service_type` 为 `text/image/video`，`provider/model_key/base_url` 标识调用配置，`apikey` 仅存加密信封，解密主密钥在数据库之外。`capability_cache` 是内部协议与能力缓存。

`enabled` 控制新操作可用性，`is_deleted` 保留历史引用，`is_default` 标识该服务默认项。默认配置必须启用且未删除，`default_service_type` 的唯一约束保证每种服务最多一个未删除默认项。配置编辑、删除及默认切换使用 `row_version`；历史调用使用独立快照，不把当前配置误当作调用时配置。

### 媒体与素材（6 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `media_files` | MIME、稳定 `storage_locator`、尺寸、字节数、实际时长及 SHA-256 | 定位值完整唯一；不保存临时签名 URL 或 Blob URL；摘要不作为全库唯一身份 |
| `assets` | 角色、场景或道具本体；名称、`label`、描述、提示词、`tags`、`scene_time`、当前图片、确认状态和版本 | `kind` 为 `character/scene/prop`；`media_id` 指向当前采用图片，`model_id` 可空；确认时必须已有图片 |
| `global_assets` | 全局素材库收录关系和排序 | `asset_id` 唯一、`position` 唯一 |
| `project_assets` | 项目素材库收录关系和排序 | `(project_id, asset_id)` 和 `(project_id, position)` 分别唯一 |
| `episode_assets` | 分集素材库收录关系和排序 | `(episode_id, asset_id)` 和 `(episode_id, position)` 分别唯一 |
| `asset_image_candidates` | 素材可选图片与文件的关系 | `(asset_id, media_id)` 唯一；只记录候选关联及创建时间，不复制媒体元数据 |

三个素材库引用同一 `assets` 本体，共享修改会影响所有引用处。删除库收录关系不等于删除素材或实际文件。素材编辑、确认与采用使用本体 `row_version`；手动创建通过唯一 `creation_key` 和初始 `creation_hash` 去重，两字段必须同时为空或同时有效。

`label` 是单一分类文本，多标签单独保存在 `tags` 数组中。数据库检查数组最多 20 项，应用校验每项字符串、长度、去空和去重。仅场景允许非空 `scene_time`。候选可来自上传或生成，上传不需要伪造 AI 调用记录；历史当前图片由专用迁移回填为候选，读取操作不隐式回填。

### 分镜与已采用结果（5 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `shot_scripts` | 分镜正文、排序、`duration_ms`、`source_excerpt`、版本、生图设置和归档时间 | 属于分集；`duration_ms` 默认 3000，范围 1000–10000；`source_excerpt` 默认空；活动顺序唯一 |
| `shot_assets` | 镜头使用的素材 | `(shot_id, asset_id)` 唯一；`(shot_id, episode_id)` 复合外键保证镜头归属 |
| `shot_images` | 镜头当前已采用图片及生成参数、`context_hash` | 每镜最多一行；图片、画幅和布局必填，状态固定 `confirmed` |
| `shot_videos` | 镜头当前已采用视频及生成参数 | 每镜最多一行；`duration` 为正数，单位毫秒，状态固定 `confirmed` |
| `media_recycle_bin` | 废弃或替换的分镜媒体与恢复所需参数 | `(shot_id, media_id)` 唯一；`reason` 为 `discarded/replaced`；图片布局/画幅与视频时长互斥 |

镜头建议时长 `shot_scripts.duration_ms`、请求视频时长 `shot_videos.duration` 与文件实际时长 `media_files.duration_ms` 各有职责，不相互覆盖。`source_excerpt` 保存生成分镜对应的连续剧本原文依据；历史行不推测原文，保持空字符串。

`shot_scripts.deleted_at` 非空代表归档，历史引用保留。`active_position` 在活动镜头上等于 `position`，归档后为 NULL；`(episode_id, active_position)` 唯一，允许归档镜头保留原顺序。排序在锁定分集和镜头后，先移到不冲突的正整数区间，再写最终顺序，同一事务提交。创建幂等键与摘要也成对维护。

集合创建、排序和分镜候选采用检查 `storyboard_version`；单镜头保存、归档检查 `row_version`；图片采用另外检查上下文摘要与当前媒体。分镜增改、排序、关联、归档或采用推进分镜集合版本，不与写作的 `content_version` 混用。素材本体变更不批量推进所有引用分集版本。

`image_settings` 是下一次生图设置，数据库检查为 JSON object 或 NULL；应用限制为 `resolution/aspect/layout`，NULL 使用默认 `2K/inherit/single`。`shot_images.context_hash` 保存采用时的 `shot-context-v1` 摘要，用当前镜头正文与建议时长、分集风格/画幅及素材内容判断已采用图片是否过时。它不包含时间戳、版本号、临时 URL 或下一次生图设置；历史 NULL 表示需要重新核对。

图片和视频通过 `(shot_id, episode_id)` 复合外键绑定分集，`media_id` 引用永久文件，`model_id` 可空。生成成功不会自动覆盖已采用结果。采用、旧结果回收及恢复必须由同一事务协调；数据库的跨表外键不会自行保证“正式结果与回收记录不能同时存在”。表结构保留视频和回收关系不等于当前 API 已提供全部视频制作与回收操作，支持范围以 API 文档为准。

### 业务来源记录（2 张）

| 表 | 职责 | 关系与边界 |
| --- | --- | --- |
| `novel_script_records` | 小说到剧本的生成来源 | 引用 `novel_id/script_id/model_id`；`script_id` 唯一；按 `batch_id` 组织同批结果 |
| `script_shot_records` | 剧本到分镜的生成来源 | 引用 `script_id/shot_id/model_id`；`(batch_id, shot_id)` 唯一 |

新 AI 业务的 `batch_id` 取任务 ID，历史批次保留，因此不加到任务表的外键。同批输出和来源关联原子写入，来源与结果必须属于同一分集，非空模型必须是文本模型。这两张表保存来源关系，不复制正文；输入快照在调用记录中，联查当前正文不能还原历史输入。手动创作不要求有生成来源记录。

### 异步任务与模型结果（3 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `async_tasks` | 任务状态、请求去重、当前动作投递、取消及执行租约 | `idempotency_key` 唯一；`request_hash` 检查同键请求；`retry_of_id` 自引用且不能指向自身 |
| `ai_generation_records` | 每次供应商调用的配置、输入、原始文本、响应、错误和时间 | `(task_id, call_no)` 唯一；引用任务与配置；`config_snapshot/request_data/response_data` 保留可追溯内容 |
| `media_assets` | 模型调用生成的图片或视频结果 | `(record_id, output_index)` 唯一，`media_id` 唯一；引用调用记录和永久文件；`row_version` 保护资产操作 |

任务仅有 `queued/running/succeeded/failed/cancelled` 五种状态；调用记录另有 `prepared/sent/succeeded/failed/unknown`。调用记录的 `unknown` 表示受理不明的内部证据，不能作为用户任务的第六种状态，也不能据此盲目重发供应商请求。

`next_action` 为 `submit/poll/save` 或 NULL；`message_status` 为 `pending/publishing/published/idle`。`message_version` 隔离过期消息，`lock_token/locked_until` 成对维护租约；任务终态必须有 `finished_at`，非终态不得有。消息发布状态不等于业务执行状态。

业务来源、来源快照、模板版本及最终输入保存在调用记录 JSON 中。业务结果与应用采用标记沿用 `response_data`；在任务行锁下同事务保存输出与处理标记，重试不重复落库。`credential_cipher` 是调用所需的加密凭据，不允许把明文凭据写入请求、响应或日志。

`media_assets` 是生成结果库，`assets` 是创作素材本体，两者通过文件关系衔接，不能互相替代。生成媒体先形成调用记录和永久文件/资产，用户采用后才进入正式分镜结果。

## 结构验证与维护

修改数据库结构时，同步维护总 SQL、Domain、适用于旧库的迁移和相关测试。`backend/tests/unit/test_domain.py` 无需数据库即可核对 21 表及列、类型、可空性、默认值、生成列、索引、CHECK、外键和依赖顺序，并验证总 SQL 只有 CREATE TABLE。

MySQL 集成测试使用 `backend/scripts/run_integration.py` 及测试 fixture 在配置的服务器上创建随机隔离库 `short_drama_<随机值>_test`，从总 SQL 初始化，结束后清理。迁移测试在该隔离库重建旧结构，检查升级、重入和数据保留；不得将业务库直接配置成测试目标。测试运行方式见开发说明。

DDL 会隐式提交，迁移无法作为一个普通事务整体回滚。停写、备份、结构核对、回填和恢复顺序按各迁移 README 执行；迁移文件存在或测试通过，都不代表任何具体业务库已经升级。
