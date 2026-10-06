# MySQL 8 数据库说明

项目当前设计包含 **93 张表**。[schema.mysql8.sql](schema.mysql8.sql) 是新库初始化的完整结构，已合并写作、素材、分镜、成片、批量生成、声音、原生音色、账号协作、Agent 和无限画布结构。原有 32 张业务表和 11 张身份与协作表保留，Agent 增加 9 张独立表，无限画布当前增加 41 张表。字段的完整类型、默认值、索引和 CHECK 以该文件为准；[SQLAlchemy Domain](../../backend/src/short_drama/domain) 与其保持一致。010 BeefAPI 新表的当前业务库执行状态见[迁移记录](migrations/2026-10-05-infinite-canvas/README.md)，不能以 SQL 已生成代替 DDL 已执行。

本文说明表的职责、关系及应用维护的约束，不另维护一份重复的字段清单。开发与测试见[开发说明](../development.md)，模块关系见[架构说明](../architecture.md)，接口见[API 文档](../api/README.md)。旧库升级使用[迁移目录](migrations/README.md)。

## 保持完整 SQL 同步

完整建表文件由当前 Domain 模型生成，包含全部表、字段、默认值、生成列、主键、索引、外键及 CHECK。修改模型后，在 `backend/` 下执行：

```powershell
uv run python scripts/export_schema.py
uv run python scripts/export_schema.py --check
```

`--check` 只读；完整 SQL 与当前模型不一致时返回非零退出码。生成脚本使用固定输出路径，不会连接或修改数据库。结构变更仍需提供并执行已有数据库的增量迁移，验证全量 SQL 能在隔离空库中执行。

## 初始化与升级

新库要求 MySQL **8.0.21+**。先由数据库管理员创建并选定空数据库，连接使用 `utf8mb4`、UTC（`+00:00`）及严格 SQL 模式（至少 `STRICT_TRANS_TABLES`），再执行完整的 `schema.mysql8.sql`。文件只包含按外键依赖顺序排列的 93 条 `CREATE TABLE`，不创建数据库、不设置连接、不导入数据，也不自动选择业务库。

已有数据库不能用全量脚本覆盖。根据实际字段、索引、约束和已执行记录判断缺少哪些迁移，按[迁移顺序](migrations/README.md)补齐；应用启动不会自动迁移。新库执行完整脚本后无需叠加历史迁移。

账号模式启用前必须按[协作部署说明](../collaboration-deployment.md)明确指定已验证的历史所有者，拆分跨范围素材和物理文件，再安装非空、外键、范围 CHECK 及账号唯一约束。注册不自动领取历史数据；结构或归属未完成时 API 与 Worker 拒绝启动。

## 身份、归属与协作（11 张）

详细决策、权限矩阵及 Goal 验证见[协作设计](../plans/2026-10-02-project-collaboration.md)。

| 表 | 职责 |
| --- | --- |
| `users` | 唯一账号名、规范化唯一邮箱、密码摘要、验证时间和状态 |
| `user_sessions` | 会话与 CSRF 摘要、过期和撤销 |
| `email_challenges` | 用途、账号、邮箱与邀请绑定的证明、失败尝试和消费 |
| `project_members` | 项目与账号唯一关系、有效/移除/退出状态 |
| `project_invitations` | 定向受邀账号或邮箱、令牌摘要、七天有效期、撤销与接受 |
| `audit_events` | 安全元数据审计，不记录正文、密码或密钥 |
| `user_model_preferences` | 本人创作上下文与模型选择 |
| `user_project_states` | 本人的项目最近打开时间 |
| `email_outbox` | 加密验证码邮件载荷、租约、发送重试及载荷清除 |
| `auth_rate_limits` | 多 API 进程共用的持久化限流计数 |
| `resource_imports` | 跨范围复制的幂等收据、快照、稳定目标、租约、重试及清理时间 |

项目只有一个 `owner_user_id`；有效协作者通过 `project_members` 加入。`projects.owner_user_id`、`ai_model_configs.owner_user_id` 和 `global_assets.user_id` 非空且引用用户。

`assets/media_files/async_tasks/generation_batches` 用 `scope_user_id/project_id` 表示个人或项目范围，CHECK 强制恰好一个非空。其余资源通过分集、分镜、素材或任务等父链确定范围。跨表引用在服务事务与 ORM 写入守卫中校验，列表、计数、JOIN 和别名查询也经过范围过滤。

`created_by/updated_by` 只记录真实作者；历史未知保持 NULL。候选以可信、不可变的作者并结合实际项目权限授权，未知作者的候选隔离。任务、批次和渲染的 `initiated_by` 限制其列表、详情、记录、预览、下载、取消、重试与恢复为本人。协作者退出后明确采用的项目作品仍可共享，未提交工作被取消，已受理调用可归档私有候选。

`episode_scripts.published_at` 与 `media_files.published_at` 在明确采用或加入业务引用的同一事务中设置。本人可读自己的候选；其他项目成员只能读取有发布证据的业务作品。替换当前作品不撤回曾采用作品，发布标记不能由普通编辑清除。公开作品响应不携带他人的任务、候选、调用记录、模型配置或冻结生成参数。旧库执行[候选隐私迁移与证据回填](migrations/2026-10-03-private-candidates/README.md)。

## Agent 创作与候选（9 张）

| 表 | 职责与读取范围 |
| --- | --- |
| `agent_conversations` | 按流程、稳定对象 ID 和任务归类隔离的分集私有会话；固定创作要求及消息/事件序号；本人且仍有项目权限才能读取 |
| `agent_messages` | 追加的用户与助手消息、引用和幂等键；继承会话私有范围 |
| `agent_runs` | 私有任务状态、检查点、预算、使用量和租约；会话内制作执行串行，补充消息持久化排队 |
| `agent_turns` | 每次决策调用的私有请求、响应、状态与使用量；继承 Run 范围 |
| `agent_tool_calls` | 私有工具意图、参数、逐任务审批与原生生成任务关联；继承 Run 范围 |
| `agent_events` | 私有状态事件；`seq` 在会话内唯一，独立于消息序号 |
| `agent_artifacts` | 本人的文本、剧本、素材、分镜和图像/视频候选；保存不可变来源、类型化引用、建议补丁与采用回执 |
| `agent_attachments` | 本人会话的文本、图片、视频、音频输入，稳定媒体引用、上传幂等键及绑定消息；上传文件保持个人媒体范围，历史消息引用保留 |
| `agent_skills` | 本人的 Markdown 技能、内容版本、摘要、启用状态与软删除；加载时冻结准确内容版本 |

Run 的触发消息、Tool 的 Turn 和 Event 的 Run 使用复合外键保证父链一致。候选的剧本与镜头引用必须属于同一分集，素材必须关联该分集，任务与媒体必须属于同一项目且互相匹配；由服务和 ORM 守卫校验跨表范围。私有会话记录不进入项目共享审计。

会话范围由 `owner_user_id/project_id/episode_id/stage/subject_type/subject_id/task_type` 标识，不能由标题、人物名称或镜头序号推断。新记录 `scope_version=1`，创建后不可编辑；旧记录 `scope_version=0` 且四个范围字段为 NULL，不混入新对象的历史。素材必须关联本集，镜头必须是本集未删除对象；列表、总数、搜索和候选入口使用同一范围。旧库执行[会话对象范围增量](migrations/2026-10-04-agent-creation-scope/README.md)。

Agent 默认开启，旧库显式执行[Agent 迁移](migrations/2026-10-02-agent-mode/README.md)与[附件/Skill 增量](migrations/20261003_agent_context.sql)；未升级环境显式设置 `AGENT_ENABLED=false`。基础账户 readiness 在功能关闭时忽略 Agent 表，启用时校验完整字段、索引、外键、CHECK 表达式与启用状态。候选 API 只供本人，采用后通过业务作品接口共享结果。

## 批量、声音与原生音色（8 张）

| 表 | 职责 |
| --- | --- |
| `generation_batches`、`generation_batch_items` | 持久化批次与子项、受控并发、快照、重试与审核 |
| `episode_sounds` | 分集声音草稿、配乐、字幕与版本 |
| `project_voice_defaults` | 项目角色的传统配音默认值 |
| `sound_media_references` | 声音对媒体的引用 |
| `project_sound_modes` | 项目传统或原生视频声音模式 |
| `character_voices` | 项目角色的原生音色绑定 |
| `shot_dialogues` | 分镜台词及原生视频对话设置 |

这些数据继承所属项目权限；个人素材复制不带入项目音色绑定。既有生产功能的详细约束与增量升级步骤见[迁移目录](migrations/README.md)。

## 物理约定

### 成片合成（3 张）

| 表 | 职责 |
| --- | --- |
| `episode_assemblies` | 每集唯一的成片草稿，保存画幅、清晰度、版本和当前成片媒体 |
| `episode_assembly_clips` | 独立片段实例、稳定 UUID、顺序、来源媒体、裁剪及静音；同一分镜可引用多次，移出轨道保留撤销与来源记录 |
| `episode_render_jobs` | 检测/预览/导出任务、不可变快照、幂等键、进度、取消、重试、租约与输出媒体 |

`media_files.video_metadata` 缓存从真实文件读取的视频信息。它不接受客户端编辑，也不使用模型请求时长作为实际时长。导出与采用分别提交，旧成片不因草稿变化而删除。完整新增结构见[合成迁移](migrations/2026-09-28-episode-assembly/README.md)。

时间轴变更见[2026-09-29 迁移](migrations/2026-09-29-assembly-timeline/README.md)。媒体元数据同时记录派生播放代理及缩略图的托管地址，签名播放 URL 不写入数据库。

| 项目 | 当前约定 |
| --- | --- |
| 存储 | InnoDB、`ROW_FORMAT=DYNAMIC`、`utf8mb4`，表默认排序规则 `utf8mb4_0900_ai_ci` |
| 精确值 | 状态、模型编码、幂等键、存储定位值等使用 `utf8mb4_0900_bin`；摘要使用 `ascii_bin` |
| 主键 | `BIGINT UNSIGNED`，由应用雪花算法生成，不使用 `AUTO_INCREMENT`；API 中 ID 以十进制字符串传递 |
| 时间 | `DATETIME(6)`，连接统一 UTC；数据库默认创建时间，应用维护修改时间，不使用 `ON UPDATE CURRENT_TIMESTAMP` |
| 历史审计 | 传统业务表允许未知审计时间为 NULL；任务、调用记录、生成媒体资产和素材候选的创建时间必须非空，前三者的修改时间也必须非空 |
| 作者审计 | `created_by`、`updated_by` 可空，旧表保留无外键形式；新操作记录真实账号，历史未知不伪造 |
| 正文 | 小说、剧本、分镜、描述和提示词使用 `MEDIUMTEXT`；大多数空正文默认 `('')` |
| 删除 | 所有外键使用 `ON DELETE RESTRICT ON UPDATE RESTRICT`，由应用显式维护关系；不会自动级联清理文件 |
| 枚举 | `VARCHAR` 配合 CHECK；标志使用 `TINYINT UNSIGNED` 配合 CHECK |
| 条件唯一 | 持久生成列 `default_service_type`、`confirmed_episode_id`、`active_position` 支持条件唯一，应用不写入这些列；模型默认值按账号隔离 |

普通索引支持列表筛选、顺序和关系查询；唯一索引维护默认配置、已确认剧本、活动镜头顺序及请求去重等不变量。CHECK 只能校验本行，跨表媒体类型、模型服务类型及业务归属仍由应用事务校验。

## 表与关系

### 项目与分集写作（4 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `projects` | 所有者、项目名称、梗概、默认 `style/aspect`、`row_version`、`archived_at` | 名称允许重名；画幅为 `16:9/9:16`；账号模式删除为归档，最近打开存入个人状态表 |
| `episodes` | 分集标题、简介、自己的制作设置、`editing_script_id`、`content_version`、`storyboard_version`、`row_version` | 属于项目；`(project_id, position)` 唯一；正文、分镜集合和设置版本分别维护 |
| `episode_novels` | 分集当前小说正文 `content` | `episode_id` 唯一，每集最多一条小说 |
| `episode_scripts` | 多份剧本正文、排序、`state` | `(episode_id, position)` 唯一；状态为 `unconfirmed/confirmed`；生成列确保每集最多一份已确认剧本 |

`episodes.editing_script_id` 表示编辑器当前选择，与已确认剧本不是同一概念。该字段不设外键，避免分集和剧本的循环依赖；应用锁定分集后检查剧本属于本集，并在删除当前剧本时维护指针。`content_version` 用于小说、剧本与编辑选择的乐观并发控制，修改时检查客户端观察到的版本。

### 模型配置（1 张）

`ai_model_configs` 每行属于一个账号，对应一种服务的一个具体模型。`service_type` 为 `text/image/video/audio`，`provider/model_key/base_url` 标识调用配置，`apikey` 仅存加密信封，解密主密钥在数据库之外。`capability_cache` 是内部协议与能力缓存。项目成员只能使用本人的配置；项目生成成果可以共享。

`runtime_profile JSON NULL` 是宿主统一管理的可信调用协议、能力与默认参数；`runtime_credentials_cipher MEDIUMTEXT NULL` 加密保存 Secret Key 和 headers，API Key 仍只有原 `apikey` 真值源。普通画布目录不再回写模型；旧 `canvas_channel_models` 保留稳定模型 ID 与选择别名，`runtime_migrated_at DATETIME(3) NULL` 标记已迁移，防止清空高级配置后重入恢复旧目录。旧库必须执行[统一模型运行配置迁移](migrations/2026-10-06-host-model-runtime/README.md)，不改历史任务冻结快照，首次回填推进模型版本并清理派生缓存。

`enabled` 控制新操作可用性，`is_deleted` 保留历史引用，`is_default` 标识该服务默认项。默认配置必须启用且未删除，`(owner_user_id, default_service_type)` 的唯一约束保证每个账号每种服务最多一个未删除默认项。配置编辑、删除及默认切换使用 `row_version`；历史调用使用独立快照，不把当前配置误当作调用时配置。

### 媒体与素材（6 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `media_files` | MIME、稳定 `storage_locator`、尺寸、字节数、实际时长及 SHA-256 | 定位值完整唯一；不保存临时签名 URL 或 Blob URL；摘要不作为全库唯一身份 |
| `assets` | 角色、场景或道具本体；名称、`label`、描述、提示词、`tags`、`scene_time`、当前图片、确认状态和版本 | `kind` 为 `character/scene/prop`；`media_id` 指向当前采用图片，`model_id` 可空；确认时必须已有图片 |
| `global_assets` | 当前账号的个人素材库收录关系和排序，路径兼容旧命名 | `asset_id` 唯一、`(user_id, position)` 唯一 |
| `project_assets` | 项目素材库收录关系和排序 | `(project_id, asset_id)` 和 `(project_id, position)` 分别唯一 |
| `episode_assets` | 分集素材库收录关系和排序 | `(episode_id, asset_id)` 和 `(episode_id, position)` 分别唯一 |
| `asset_image_candidates` | 本人的素材可选图片与文件关系 | `(created_by, asset_id, media_id)` 唯一；未知创建人隔离，不复制媒体元数据 |

同一项目的项目库与分集库可以引用同一 `assets` 本体，共享修改会影响本项目内的引用处。个人或其他项目导入时，通过 `resource_imports` 复制本体、当前媒体和参考图片，物理存储独立；不复制私有模型配置、生成历史或项目音色。删除库收录关系不等于删除素材或实际文件。素材编辑、确认与采用使用本体 `row_version`；手动创建通过唯一 `creation_key` 和初始 `creation_hash` 去重，两字段必须同时为空或同时有效。

`label` 是单一分类文本，多标签单独保存在 `tags` 数组中。数据库检查数组最多 20 项，应用校验每项字符串、长度、去空和去重。仅场景允许非空 `scene_time`。候选可来自上传或生成，上传不需要伪造 AI 调用记录；历史当前图片由专用迁移回填为候选，读取操作不隐式回填。

### 分镜与已采用结果（5 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `shot_scripts` | 分镜正文、排序、`duration_ms`、`source_excerpt`、版本、生图设置和归档时间 | 属于分集；`duration_ms` 默认 3000，范围 1000–10000；`source_excerpt` 默认空；活动顺序唯一 |
| `shot_assets` | 镜头使用的素材 | `(shot_id, asset_id)` 唯一；`(shot_id, episode_id)` 复合外键保证镜头归属 |
| `shot_images` | 镜头当前已采用图片及生成参数、`context_hash` | 每镜最多一行；图片、画幅和布局必填，状态固定 `confirmed` |
| `shot_videos` | 镜头当前已采用视频及生成参数、`context_hash`、`first_frame_media_id` | 每镜最多一行；`duration` 为正数，单位毫秒，状态固定 `confirmed`；首帧可空且引用媒体文件 |
| `media_recycle_bin` | 废弃或替换的分镜媒体与恢复所需参数 | `(shot_id, media_id)` 唯一；`reason` 为 `discarded/replaced`；图片布局/画幅与视频时长互斥 |

镜头建议时长 `shot_scripts.duration_ms`、请求视频时长 `shot_videos.duration` 与文件实际时长 `media_files.duration_ms` 各有职责，不相互覆盖。`source_excerpt` 保存生成分镜对应的连续剧本原文依据；历史行不推测原文，保持空字符串。

`shot_scripts.video_prompt` 默认空，表示自动使用分镜默认视频内容；`video_settings` 为可空 JSON 对象，当前保存清晰度，NULL 读取为 720p。两者通过分镜版本控制保存，不进入图片上下文摘要。视频摘要 v2 覆盖分镜上下文、当前参考图媒体 ID、全能参考模式、视频提示词和设置；不包含当前视频或 row_version，避免采用后自身过期。全能参考图及排版保存在生成记录的输入与来源快照中；`shot_videos.first_frame_media_id` 仅保留历史或通用首帧任务来源，全能参考任务不填充，不复用该列冒充首帧。

`shot_scripts.deleted_at` 非空代表归档，历史引用保留。`active_position` 在活动镜头上等于 `position`，归档后为 NULL；`(episode_id, active_position)` 唯一，允许归档镜头保留原顺序。排序在锁定分集和镜头后，先移到不冲突的正整数区间，再写最终顺序，同一事务提交。创建幂等键与摘要也成对维护。

集合创建、排序和分镜候选采用检查 `storyboard_version`；单镜头保存、归档检查 `row_version`；图片采用另外检查上下文摘要与当前媒体。分镜增改、排序、关联、归档或采用推进分镜集合版本，不与写作的 `content_version` 混用。素材本体变更不批量推进所有引用分集版本。

`image_settings` 是下一次生图设置，数据库检查为 JSON object 或 NULL；应用限制为 `resolution/aspect/layout`，NULL 使用默认 `2K/inherit/single`。`shot_images.context_hash` 保存采用时的 `shot-context-v1` 摘要，用当前镜头正文与建议时长、分集风格/画幅、上传参考图及素材内容判断已采用图片是否过时。它不包含时间戳、版本号、临时 URL 或下一次生图设置；历史 NULL 表示需要重新核对。

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

## 持久化生成参考图

`assets.reference_media_ids` 与 `shot_scripts.reference_media_ids` 为默认空数组的 JSON，按上传顺序保存十进制媒体 ID 字符串，最多 16 张。服务层校验引用图片存在、类型合法并去重；不自动创建候选或采用结果。移除只解除引用，文件仍供历史任务读取。素材/分镜内容摘要在参考列表非空时包含该字段，空列表保持旧摘要兼容。上传或移除推进所属 row_version，分镜还推进 storyboard_version。

## 结构验证与维护

修改数据库结构时，同步维护总 SQL、Domain、适用于旧库的迁移和相关测试。`backend/tests/unit/test_domain.py` 无需数据库即可核对当前全部表及列、类型、可空性、默认值、生成列、索引、CHECK、外键和依赖顺序，并验证总 SQL 只有 CREATE TABLE。`backend/scripts/export_schema.py` 从 Domain 导出完整 SQL，旧库仍使用专用增量迁移。

MySQL 集成测试使用 `backend/scripts/run_integration.py` 及测试 fixture 在配置的服务器上创建随机隔离库 `short_drama_<随机值>_test`，从总 SQL 初始化，结束后清理。迁移测试在该隔离库重建旧结构，检查升级、重入和数据保留；不得将业务库直接配置成测试目标。测试运行方式见开发说明。

DDL 会隐式提交，迁移无法作为一个普通事务整体回滚。停写、备份、结构核对、回填和恢复顺序按各迁移 README 执行；迁移文件存在或测试通过，都不代表任何具体业务库已经升级。

## 无限画布基础存储

当前项目增加固定的 `workspace_mode`，历史项目默认 `standard`。`infinite_canvas` 使用 `project_canvas_settings` 的主画布指针和 `project_canvases` 的一对多归属。源画布稳定键 source_key 与数据库 ID 分开，源节点、连线 ID 保持原值；几何使用 DOUBLE 保留拖拽小数，数据库 ID/版本对外使用十进制字符串。

当前图按 canvas_nodes/canvas_edges 保存，时间线和导演子文档分别放在 canvas_timelines/canvas_director_scenes。canvas_revisions 保存历史共享快照，canvas_revision_user_states 保存作者快照投影。快照拥有的私人状态与媒体/文件引用、上传拥有的分片、素材拥有的资源引用和分类归属允许 ON DELETE CASCADE；资源与作品本身的外键保持 RESTRICT，避免连带删除文件或作品。

canvas_user_states、canvas_node_user_states、canvas_user_media_references 隔离每名成员的偏好、提示词、任务参数和对话；canvas_workspace_user_states 保存个人模型选择和生成偏好，不保存供应商密钥。canvas_write_receipts 保证写入重试，归档后的删除回执依然按当前成员身份校验。媒体稳定引用分为当前共享、快照共享和本人私有引用，不保存签名 URL 作为媒体身份。

canvas_model_catalogs 保存本人原版渠道目录，公开 JSON 与 API Key、Secret Key、headers 的独立加密信封分离；canvas_channel_models 将本人源渠道/模型稳定绑定到真实 AIModelConfig，删除与重加不更换身份。目录和偏好共用本人版本事务，宿主配置不重复显示成自定义渠道。独立模型测试沿用现有私有任务及真实媒体表，test-only 配置从正常模型选择排除，测试历史不创建画布或发布作品。详见 [009 模型目录迁移](migrations/2026-10-05-infinite-canvas/README.md)。

canvas_beefapi_connections 每人一行，保存公开授权状态、密文设备码/API Key、版本、下一次轮询时间及数据库租约。官方托管目录沿用上述渠道与模型绑定表；托管 Key 不复制进渠道目录信封，自定义 headers 仍加密保存。scheduler 按到期租约恢复本人授权，项目成员无权读取他人连接、凭据或账户资料。详见 [010 BeefAPI 连接迁移](migrations/2026-10-05-infinite-canvas/010-canvas-beefapi-connection.sql)。

canvas_task_bindings 冻结本人任务的操作身份、来源节点和请求摘要；canvas_task_media_references 保护实际来源文件；canvas_results 以任务/输出槽唯一保存不可变已归档产物和明确绑定回执。canvas_task_text_deltas 以任务绑定/序号唯一保存供应商实际正文增量，并通过 generation_record_id 固定调用记录。它们仅本人和当前项目权限可见，普通编辑不能改写出处或增量。增量每条/任务/账号有配额，Worker 写入检查执行租约，过期清理不删除最终正文或绑定作品。

canvas_resource_uploads / canvas_resource_chunks 保存本人上传会话、预留 ID、分片摘要、到期时间与完成结果。图片、视频、音频继续使用 media_files；file 类字节使用 canvas_binary_resources，引用由 canvas_binary_references / canvas_user_binary_references 保留。只在进入共享图时发布本人同项目资源，私人参数中的引用不会发布。

canvas_resource_uploads.mode 明确区分 multipart/chunked/copy。canvas_resource_copy_sources 按 upload_id 一对一记录私人副本来源和不可变元数据快照；复制未完成时，以 source_media_id 或 source_binary_id 外键保护源资源。完成或过期清理后释放来源外键，保留 original_resource_id 和来源快照，完成的副本不再依赖源项目权限或源文件存活。来源表通过本人上传记录限定读取，目标项目成员看不到复制来源。上传记录被彻底删除时级联移除这条来源记录，永久重试保护仍由删除回执负责。来源外键跨项目仅用于已授权复制的暂时保护，不允许共享画布建立跨项目媒体引用。

canvas_resource_deletions 保存永久删除资源的持久清理任务和原上传身份墓碑。素材、上传、资源记录与删除回执在一个事务内移除/创建，MinIO 删除在提交后由 scheduler 重试。原 resource_id、upload_id 不再设置到已删除记录的外键；(user_id,idempotency_hash)、resource_id、upload_id 各自唯一，状态为 pending/completed/retained。跨账号共用物理定位值时保留文件；完成后保留回执，旧请求返回 410，避免未知响应重试重新创建已经删除的资源。

canvas_library_assets / canvas_library_asset_references 保存私人的源素材文档与其稳定资源关系，不复用标准角色/场景素材表。canvas_library_folders / canvas_library_folder_items 保存个人分类与单一分类归属，分类删除通过关系清理将素材回到未分类。folderId 仅在输出中从关系表投影，避免 JSON 与分类表分别可写。

canvas_project_folders / canvas_project_folder_items 是本人项目文件夹及画布归属，与素材分类分开。`(user_id,source_key)` 和 `(user_id,canvas_id)` 分别唯一；归属以 `(user_id,folder_key)` 复合外键固定到同一作者文件夹，不透明键最多 80 字符。封面使用 media_files/canvas_binary_resources 二选一外键，墓碑禁止继续保留封面引用。文件夹不物理删除，防止旧客户端 PUT 复活；画布真正删除时可级联清除其私人归属，但文件夹删除不级联删除作品。共享文档与共享历史不含 folderId，只有本人投影携带。撤权后的分类清理不授予作品读取/修改权限。

增量升级和预检见[无限画布迁移](migrations/2026-10-05-infinite-canvas/README.md)，已接通的行为见[画布 API](../api/canvases.md)。当前已有上传来源资源的删除保护和回收；完整媒体加工、尚未迁入业务的引用及助手功能仍需后续实体与服务迁移。

canvas_creation_attempts / canvas_creation_resources 保存本人首次完整创建的固定请求与资源准备进度。attempt 的 `(user_id,idempotency_key)`、`(user_id,source_key)` 唯一；resource 的目标 ID 与定位值唯一，通过 `(attempt_id,user_id)` 复合外键固定作者。复制期间通过来源媒体/二进制外键防删除，复制完成释放外键并固定来源快照；最终建图、文件与回执同一事务提交。准备过程不创建可见半成品项目。过期清理复用复制锁，先持久标记需重新复制再删除字节，保留请求与目标身份；ready/attached 不进入清理。

canvas_drawings 保存 `(canvas_id,source_key)` 唯一的绘图身份、当前版本及删除墓碑；canvas_drawing_versions 保存 `(drawing_id,row_version)` 唯一的不可变 Excalidraw 文档、预览/成品身份与摘要。版本的 document_json 内 snapshot 采用精确 JSON 字符串封装，并以内部 `$snapshot_encoding` 标记，防止 MySQL JSON 浮点转码改变笔画坐标；读取解码，外部合同仍为原始 snapshot 对象。canvas_drawing_media_references 保护每版实际引用的图片，版本内部引用可随版本 CASCADE，图片外键保持 RESTRICT。当前绘图不维护第二份可编辑全量 JSON，读取由身份表与对应版本组合。已保存作品按项目成员共享，未提交本机草稿不进这些表。

canvas_revision_drawing_references 通过同画布复合外键将整图历史绑定到不可变绘图版本，版本不能在被引用时删除；历史淘汰可级联删除自己的绑定。历史图保存稳定预览定位，恢复绘图使用原版本正文并新增单调递增版本，图、绘图、媒体引用和写回执在同一事务提交。恢复同时检查打开列表时的图版本与绘图版本/墓碑，不能覆盖列表打开后另行保存的笔画。删除绘图仍保留全部版本与资源，版本保留/回收策略及跨项目绘图复制尚须继续迁移。
