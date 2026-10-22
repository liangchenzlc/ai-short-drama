# MySQL 8 数据库说明

当前 2026-10-21 创作模式结构以[完整 SQL](schema/schema.mysql8.sql)和[本次迁移记录](migrations/2026-10-21-creation-mode/README.md)为准。单层项目移除了 `episodes`、`global_assets/project_assets/episode_assets`、成员及邀请；原 `episode_*` 内容表改为 `project_*`，`assets` 直接包含非空 `project_id/position`。项目保留正文／分镜版本和编辑指针，删除梗概／画幅／风格。下文此前表数与协作／分集描述为历史背景，不作为新结构初始化依据。

当前完整结构包含 **117 张表**，[schema.mysql8.sql](schema/schema.mysql8.sql) 用于初始化空数据库，与[SQLAlchemy Domain](../../backend/src/short_drama/domain)同步。当前 `short_drama` 已执行[全局助手 001–009 迁移](migrations/2026-10-21-agent-system/README.md)，Agent 共二十五表且 readiness 为 `ready`。历史阶段的表数不代表当前结构；字段类型、默认值、索引和 CHECK 以完整 SQL 为准，不能以 SQL 已生成代替 DDL 已执行，也不能以结构就绪代替供应商验收。

本文说明表的职责、关系及应用维护的约束，不另维护一份重复的字段清单。开发与测试见[开发说明](../instruction/开发运行与验证.md)，模块关系见[架构说明](../instruction/系统架构与数据流.md)，接口见[API 文档](../api/README.md)。旧库升级使用[迁移目录](migrations/README.md)。

标准模式创作新增镜头名称、受限景别、剧本 Delta 和私人生成草稿表，见[2026-10-13 迁移及实际执行记录](migrations/2026-10-13-standard-creation-workflow/README.md)。正文与格式同事务保存，私人草稿按本人、稳定对象和用途隔离，生成引用继续校验来源版本及上下文。

标准图片组见[2026-10-16 增量及实际执行记录](migrations/2026-10-16-image-groups/README.md)。`image_groups` 保存当前组／历史组、生成数量及主图版本，目标为素材或镜头二选一；`active_slot=1` 与目标唯一约束保证只有一组当前作品，历史组为 NULL。`image_group_items` 以 `(group_id, output_index)` 固定 1–25 的图位并保存单图版本、原图历史；`image_operations` 按 `(created_by, request_key)` 幂等冻结处理原图及来源，记录私人执行状态。整组和全部作品媒体按来源范围共享，任务、提示词及操作仍仅本人可见。新增三表及两个普通索引已在当前 `short_drama` 实际创建，未批量回写现有作品；旧主图首次读取时建立兼容图片组。

## 保持完整 SQL 同步

标准成片多轨见[2026-10-21 增量与执行记录](migrations/2026-10-21-assembly-editor/README.md)。`episode_assembly_clips` 可引用独立视频／图片，`source_type` 区分来源，`display_duration_ms` 保存图片时长，`transform` 保存构图；`episode_render_jobs` 扩展转写和声音分析类型。多轨声音及字幕继续使用现有声音文档 JSON，CAS 同时校验画面与声音版本；任务结果仅对本人可见。当前 `short_drama` 已执行两条 ALTER，仍为 107 张表。

完整建表文件由当前 Domain 模型生成，包含全部表、字段、默认值、生成列、主键、索引、外键及 CHECK。修改模型后，在 `backend/` 下执行：

```powershell
uv run python scripts/export_schema.py
uv run python scripts/export_schema.py --check
```

`--check` 只读；完整 SQL 与当前模型不一致时返回非零退出码。生成脚本使用固定输出路径，不会连接或修改数据库。结构变更仍需提供并执行已有数据库的增量迁移，验证全量 SQL 能在隔离空库中执行。

## 初始化与升级

新库要求 MySQL **8.0.21+**。先由数据库管理员创建并选定空数据库，连接使用 `utf8mb4`、UTC（`+00:00`）及严格 SQL 模式（至少 `STRICT_TRANS_TABLES`），再执行完整的 `schema.mysql8.sql`。文件包含按外键依赖顺序排列的 113 条 `CREATE TABLE`，角色当前版本及会话摘要的循环外键在相关表建立后用命名 `ALTER TABLE` 添加；不创建数据库、不设置连接、不导入数据，也不自动选择业务库。

已有数据库不能用全量脚本覆盖。根据实际字段、索引、约束和已执行记录判断缺少哪些迁移，按[迁移顺序](migrations/README.md)补齐；应用启动不会自动迁移。新库执行完整脚本后无需叠加历史迁移。

账号模式启用前必须按[协作部署说明](../instruction/账号与共同创作部署说明.md)明确指定已验证的历史所有者，拆分跨范围素材和物理文件，再安装非空、外键、范围 CHECK 及账号唯一约束。注册不自动领取历史数据；结构或归属未完成时 API 与 Worker 拒绝启动。

## 身份、归属与协作（11 张）

详细决策、权限矩阵及 Goal 验证见[协作设计](../plans/2026-10/2026-10-02-用户权限与共同创作设计.md)。

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

`episode_scripts.published_at` 与 `media_files.published_at` 在明确采用、加入业务引用或标准图片组归档的同一事务中设置。标准图片组的全部图位及原图历史共享，私人任务和提示词仍隔离。本人可读自己的候选；其他项目成员只能读取有发布证据的业务作品。替换当前作品不撤回曾发布作品，发布标记不能由普通编辑清除。公开作品响应不携带他人的任务、候选、调用记录、模型配置或冻结生成参数。旧库执行[候选隐私迁移与证据回填](migrations/2026-10-03-private-candidates/README.md)。

## Agent 会话、任务、记忆与偏好（25 张）

| 表 | 职责与读取范围 |
| --- | --- |
| `agent_conversations` | V3 按账号隔离全局会话，不绑定作品；旧项目／对象会话保留原范围与历史 |
| `agent_messages` | 追加的用户与助手消息、引用和幂等键；继承会话私有范围 |
| `agent_runs` | 私有任务状态、检查点、预算、使用量和租约；会话内制作执行串行，补充消息持久化排队 |
| `agent_turns` | 每次决策调用的私有请求、响应、状态与使用量；继承 Run 范围 |
| `agent_tool_calls` | 私有工具意图、参数、逐任务审批与原生生成任务关联；继承 Run 范围 |
| `agent_events` | 私有状态事件；`seq` 在会话内唯一，独立于消息序号 |
| `agent_artifacts` | 本人的文本、剧本、素材、分镜和图像/视频候选；保存不可变来源、类型化引用、建议补丁与采用回执 |
| `agent_attachments` | 本人会话的文本、图片、视频、音频输入，稳定媒体引用、上传幂等键及绑定消息；上传文件保持个人媒体范围，历史消息引用保留 |
| `agent_skills` | 本人的 Markdown 技能、内容版本、摘要、启用状态与软删除；加载时冻结准确内容版本 |
| `agent_tasks`、`agent_task_inputs`、`agent_task_controls`、`agent_operations` | 逻辑任务、不可变补充输入、有序控制及工具完整回执；同会话串行，工具边界应用控制 |
| `agent_memory_items`、`agent_memory_revisions`、`agent_memory_sources`、`agent_memory_forget_rules` | 本人账号／会话／明确作品的事实、版本、来源及遗忘防复活 |
| `agent_conversation_summaries`、`agent_memory_jobs` | 有来源链的阶段摘要，以及摘要／抽取／删除清理的可靠作业与租约 |
| `agent_preferences` | 每账号唯一的助手媒体总开关和策略版本；未保存时默认开启，保存过的关闭不重置 |
| `agent_media_requests` | 本人媒体请求、原生任务关联、冻结目标、数量预占和完整结算 |
| `agent_skill_versions`、`agent_skill_settings` | 不可变内容／资料版本，以及本人启停与调用方式 |
| `agent_discussion_revisions` | 纯讨论编辑／重答的不可变修订、原请求与运行身份 |
| `agent_batch_undos` | 原操作下明确选择的逆向差异、幂等身份和独立撤销结果 |

Run 的触发消息、Tool 的 Turn、Event 的 Run 及任务操作使用复合外键保证父链一致。作品、素材、分镜和媒体由服务及 ORM 守卫校验真实归属。私人会话、记忆和账号助手偏好不进入项目共享审计。

当前 AI 创作助手记录 `scope_version=3`，`project_id/stage/subject_type/subject_id/task_type` 全部为 NULL，任务的主动主体、作品记忆与写入目标分别管理。正文及私人媒体只在获准来源范围按需读取；有界标准／画布结构、图片组主图及显式记忆工具的业务效果与回执同事务。媒体生成及完整 Skill 系统仍在实施，不能以旧 V1 功能推断 V3 能力。接口和边界见[助手 API](../api/assistant.md)。

V0/V1 对象历史与 V2 项目纯聊天保留原身份，不合并为 V3，不继承旧授权。前端历史只读，旧兼容 API 仍须按其实际权限及来源版本处理；标题、人物名称和镜头序号不能推断范围。旧库按[Agent 系统迁移](migrations/2026-10-21-agent-system/README.md)补齐当前字段、表、CHECK、索引与外键。

Agent 默认开启，旧库显式执行[Agent 迁移](migrations/2026-10-02-agent-mode/README.md)与[附件/Skill 增量](migrations/20261003_agent_context.sql)；未升级环境显式设置 `AGENT_ENABLED=false`。基础账户 readiness 在功能关闭时忽略 Agent 表，启用时校验完整字段、索引、外键、CHECK 表达式与启用状态。候选 API 只供本人，采用后通过业务作品接口共享结果。

## 批量、声音与原生音色（8 张）

| 表 | 职责 |
| --- | --- |
| `generation_batches`、`generation_batch_items` | 持久化批次与子项、受控并发、快照、重试与审核 |
| `episode_sounds` | 分集声音草稿、配乐、字幕与版本 |
| `project_voice_defaults` | 项目角色的传统配音默认值 |
| `sound_media_references` | 声音对媒体的引用 |
| `project_sound_modes` | 项目传统或原生视频声音模式 |
| `character_voices` | 项目角色的原生音色绑定；record_id／media_id 成对为空表示已撤销绑定，保留递增版本行 |
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

### 媒体与素材（8 张）

| 表 | 职责与关键字段 | 关系和约束 |
| --- | --- | --- |
| `media_files` | MIME、稳定 `storage_locator`、尺寸、字节数、实际时长及 SHA-256 | 定位值完整唯一；不保存临时签名 URL 或 Blob URL；摘要不作为全库唯一身份 |
| `assets` | 角色、场景或道具本体；名称、`label`、描述、提示词、`tags`、`scene_time`、当前图片、确认状态和版本 | `kind` 为 `character/scene/prop`；`media_id` 指向当前采用图片，`model_id` 可空；确认时必须已有图片 |
| `global_assets` | 当前账号的个人素材库收录关系和排序，路径兼容旧命名 | `asset_id` 唯一、`(user_id, position)` 唯一 |
| `project_assets` | 项目素材库收录关系和排序 | `(project_id, asset_id)` 和 `(project_id, position)` 分别唯一 |
| `episode_assets` | 分集素材库收录关系和排序 | `(episode_id, asset_id)` 和 `(episode_id, position)` 分别唯一 |
| `asset_image_candidates` | 本人的素材可选图片与文件关系 | `(created_by, asset_id, media_id)` 唯一；未知创建人隔离，不复制媒体元数据 |
| `asset_versions` | 角色不可变规范快照、独立 `version`、来源编辑号、内容摘要与真实建版审计 | `(asset_id, id)`、`(asset_id, version)` 唯一；父 Asset 外键 RESTRICT；不按内容 hash 复用旧版本 |
| `asset_version_media_references` | 每版表示图及生成参考的用途、角色、顺序和真实媒体身份 | `(asset_id, version_id, purpose, ordinal)` 唯一；版本复合外键与 MediaFile 外键均 RESTRICT |

同一项目的项目库与分集库可以引用同一 `assets` 本体，共享修改会影响本项目内的引用处。个人或其他项目导入时，通过 `resource_imports` 复制本体、当前媒体和参考图片，物理存储独立；不复制私有模型配置、生成历史或项目音色。删除库收录关系不等于删除素材或实际文件。素材编辑、确认与采用使用本体 `row_version`；手动创建通过唯一 `creation_key` 和初始 `creation_hash` 去重，两字段必须同时为空或同时有效。

`label` 是单一分类文本，多标签单独保存在 `tags` 数组中。数据库检查数组最多 20 项，应用校验每项字符串、长度、去空和去重。仅场景允许非空 `scene_time`。候选可来自上传或生成，上传不需要伪造 AI 调用记录；历史当前图片由专用迁移回填为候选，读取操作不隐式回填。

`assets.current_version_id` 只用于角色，允许 NULL，并以 `(id, current_version_id)` 复合外键指向 `asset_versions(asset_id, id)`，防止跨资产指针。ORM 使用显式命名 `fk_assets_current_version` 与 `use_alter` 化解建表循环。普通 HTTP 输入不开放该指针；历史行只能由明确维护入口回填，读取和应用启动不补版。删除有历史的角色或历史所用媒体会受到 RESTRICT 保护。

应用工厂和角色回填使用 `AppSession`，精确拒绝 Asset 及两个角色历史 mapper 的三个 legacy bulk API；其他 mapper 沿用原行为。事件保护拒绝角色历史实体修改及 Session DML 指针绕写，独立 Connection 仍是可信迁移边界，不将该保护描述为所有 SQLAlchemy Session 的全面限制。

角色快照同时保存真实平面字段、完整 `definition` JSON 扩展、`definition_origin`、表示图和独立生成参考。表示图角色为 `primary/front/side/back/turnaround_sheet/expression_sheet`；生成参考的 role 为非空约束下的空字符串，不能冒充多视角作品。当前主图必须存在于明确表示图槽位。内容 hash 包含定义来源类型和有业务顺序的数组，仅排除 `source_audit`；状态、编辑号、作者、时间和模型偏好不建立作品内容版。相同当前内容不新增，A→B→A 则建立新第三版。

旧库结构与数据操作分别使用 [019 迁移](migrations/2026-10-05-infinite-canvas/019-asset-versions.sql)、`asset_version_migration.py` 与 `backfill_asset_versions.py`。后者只保存旧角色当前快照为 v1，来源编辑号保留实际值，不推造过去版本、项目关联或声音 ready；完整结构化定义只沿可信版本保留。角色 v4 已应用；随机临时 MySQL 六模块实际 81 passed / 1 skipped，跳过项为实际 MinIO 导入字节验证。2026-10-11 当前 `short_drama` 的 019 七条结构 DDL 与独立 verify 已完成，原资产/画布指纹不变，版本/版本媒体/当前指针均 0；未执行 backfill 或同版部署，准确语句与边界见上述迁移记录。

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

2026-10-16 的[画布风格与兼容播放迁移](migrations/2026-10-16-canvas-alignment/README.md)
已在 `short_drama` 执行并重入核验，标准模式 `Project.style` 保持原行为。

`canvas_style_profiles` 以 `scope_user_id` 保存本人原件、以 `project_id` 保存来源项目完整
快照，CHECK 要求两者恰有一个。项目快照唯一，个人导入键按账号唯一；独立 `row_version`
用于 CAS，快照内 `revision` 仍是语义版本。个人软删除不修改项目快照。
`canvas_style_media_references` 对风格和 `MediaFile` 建立 RESTRICT 外键，保护实际引用的
永久媒体；来源快照仅接纳同项目已发布媒体。`canvas_style_preferences` 按账号与系统预设
唯一保存收藏和最近使用。三表和 `idx_canvas_personal_styles` 已实际创建。

本地任务 CHECK 现允许 `resource_playback`，深度与兼容播放成功任务须有输出媒体，
已处置任务沿原处置回执例外。兼容副本是独立 `MediaFile`，原文件身份及下载内容不变。
写对象前在 `result_json.playback_staging` 持久登记临时定位，取消、租约失效或进程中断后
由调度器继续清理未登记文件，已登记输出不被误删。云端转写使用
`cloud_submission_started` 防止未知提交重发。


`canvas_local_jobs` 保存本人的字幕转写、深度捕捉及兼容播放任务，固定操作键、请求、来源媒体外键、
定位、SHA/长度及画布版本，私人素材可无项目归属。项目任务同时约束项目/画布复合外键，
作者/操作键唯一，HTTP 会话不能改写快照、租约或执行回执；来源外键保护排队和未处置的历史输入。
2026-10-09 已在当前 `short_drama` 实际执行014的 `CREATE TABLE canvas_local_jobs`、
`CREATE INDEX idx_canvas_local_author` 和 `CREATE INDEX idx_canvas_local_schedule`。
预检仅缺此表且无漂移；执行后再次核验 `ready/missing=[]/changed=[]`。未改写既有业务行，
ORM、完整SQL、001及014同步；执行与原界面验收范围另见实施台账。

`canvas_render_jobs` 保存本人独立的本地时间线渲染任务，冻结原请求、编译计划、画布版本和
服务端媒体定位/摘要；作者与 `client_operation_id` 唯一。数据库租约、消息版本和取消标志
防止过期执行器改写新尝试；成片通过真实 `media_files` 外键与不可变完成回执关联。
`canvas_render_media_references` 用同作者/项目/画布复合外键绑定任务，来源文件使用
RESTRICT 外键保护。该路径不创建供应商模型、AI任务或虚构的分集合成记录。

2026-10-09 已在当前业务库 `short_drama` 实际执行013的两条 `CREATE TABLE` 和任务作者、
调度两条 `CREATE INDEX`；预检后只缺这两张表，执行后 `ready`、无缺表/结构漂移，未改旧
业务数据。完整SQL、001及013与ORM同步。DDL完成不代表Worker、MinIO或原UI验收通过。

本地与渲染任务的 `disposal_receipt_id` 以 RESTRICT 外键保留 `canvas_write_receipts`
处置出处，各有一个处置索引。016只认可精确旧版来源列、成功输出 CHECK 和原外键，
两表分别用一条原子 ALTER 升级；本地来源改为可空，并以 `ck_canvas_local_source`
维持普通任务必填来源。无租约的终态、空请求/快照（渲染另需空计划）和
`canvas_work_disposed` 是使用处置回执的 CHECK 前提，成功输出只有此时可释放。
本地来源外键由原自动名改为 `fk_canvas_local_source`，避免 MySQL 在同一 ALTER 中
拒绝重建同名外键；新旧约束都按完整列、目标和 RESTRICT 规则核验，未知漂移拒绝升级。
ORM、完整SQL、001、013、014与 [016增量](migrations/2026-10-05-infinite-canvas/016-canvas-local-disposal.sql)
同步，仍为99张表/47张画布表。普通HTTP会话不能写处置出处或释放冻结媒体引用。
2026-10-10 08:58（UTC+8）已在当前 `short_drama` 实际执行016的两条 ALTER：
本地表新增处置列、外键/索引、来源与处置 CHECK，来源可空并重建原 RESTRICT 外键；
渲染表新增处置列、外键/索引与处置 CHECK，两表更新成功输出 CHECK。
预检仅缺两项精确旧版升级、无漂移；执行后独立回读 `ready/missing=[]/changed=[]`，
表数99→99，两张任务表行数分别0→0。未清理业务行/字节或重启API/Worker/scheduler；
执行报告在忽略的 `backend/.runtime/local-disposal-ddl-report.json`。应用不自动迁移。

当前项目增加固定的 `workspace_mode`，历史项目默认 `standard`。`infinite_canvas` 使用 `project_canvas_settings` 的主画布指针和 `project_canvases` 的一对多归属。源画布稳定键 source_key 与数据库 ID 分开，源节点、连线 ID 保持原值；几何使用 DOUBLE 保留拖拽小数，数据库 ID/版本对外使用十进制字符串。

当前图按 canvas_nodes/canvas_edges 保存，时间线和导演子文档分别放在 canvas_timelines/canvas_director_scenes。canvas_revisions 保存历史共享快照，canvas_revision_user_states 保存作者快照投影。快照拥有的私人状态与媒体/文件引用、上传拥有的分片、素材拥有的资源引用和分类归属允许 ON DELETE CASCADE；资源与作品本身的外键保持 RESTRICT，避免连带删除文件或作品。

canvas_user_states、canvas_node_user_states、canvas_user_media_references 隔离每名成员的偏好、提示词、任务参数和对话；canvas_workspace_user_states 保存个人模型选择和生成偏好，不保存供应商密钥。canvas_write_receipts 保证写入重试，归档后的删除回执依然按当前成员身份校验。媒体稳定引用分为当前共享、快照共享和本人私有引用，不保存签名 URL 作为媒体身份。

canvas_model_catalogs 保存本人原版渠道目录，公开 JSON 与 API Key、Secret Key、headers 的独立加密信封分离；canvas_channel_models 将本人源渠道/模型稳定绑定到真实 AIModelConfig，删除与重加不更换身份。目录和偏好共用本人版本事务，宿主配置不重复显示成自定义渠道。独立模型测试沿用现有私有任务及真实媒体表，test-only 配置从正常模型选择排除，测试历史不创建画布或发布作品。详见 [009 模型目录迁移](migrations/2026-10-05-infinite-canvas/README.md)。

canvas_beefapi_connections 已于 2026-10-15 随 BeefAPI 官方连接功能整体移除并删除，历史结构见 [010 BeefAPI 连接迁移](migrations/2026-10-05-infinite-canvas/010-canvas-beefapi-connection.sql) 与[移除记录](migrations/2026-10-15-remove-beefapi/README.md)。

canvas_task_bindings 冻结本人任务的操作身份、来源节点和请求摘要；canvas_task_media_references 保护实际来源文件；canvas_results 以任务/输出槽唯一保存不可变已归档产物和明确绑定回执。canvas_task_text_deltas 以任务绑定/序号唯一保存供应商实际正文增量，并通过 generation_record_id 固定调用记录。它们仅本人和当前项目权限可见，普通编辑不能改写出处或增量。增量每条/任务/账号有配额，Worker 写入检查执行租约，过期清理不删除最终正文或绑定作品。

canvas_resource_uploads / canvas_resource_chunks 保存本人上传会话、预留 ID、分片摘要、到期时间与完成结果。图片、视频、音频继续使用 media_files；file 类字节使用 canvas_binary_resources，引用由 canvas_binary_references / canvas_user_binary_references 保留。只在进入共享图时发布本人同项目资源，私人参数中的引用不会发布。

canvas_resource_uploads.mode 明确区分 multipart/chunked/copy。canvas_resource_copy_sources 按 upload_id 一对一记录私人副本来源和不可变元数据快照；复制未完成时，以 source_media_id 或 source_binary_id 外键保护源资源。完成或过期清理后释放来源外键，保留 original_resource_id 和来源快照，完成的副本不再依赖源项目权限或源文件存活。来源表通过本人上传记录限定读取，目标项目成员看不到复制来源。上传记录被彻底删除时级联移除这条来源记录，永久重试保护仍由删除回执负责。来源外键跨项目仅用于已授权复制的暂时保护，不允许共享画布建立跨项目媒体引用。

canvas_resource_deletions 保存永久删除资源的持久清理任务和原上传身份墓碑。素材、上传、资源记录与删除回执在一个事务内移除/创建，MinIO 删除在提交后由 scheduler 重试。原 resource_id、upload_id 不再设置到已删除记录的外键；(user_id,idempotency_hash)、resource_id、upload_id 各自唯一，状态为 pending/completed/retained。跨账号共用物理定位值时保留文件；完成后保留回执，旧请求返回 410，避免未知响应重试重新创建已经删除的资源。

早期已完成创建但没有正式上传记录的媒体也复用此表，以唯一 `creation_resource_id`
和 `disposal_receipt_id` 的 RESTRICT 外键保留真实出处。`ck_canvas_deletion_origin`
要求上传 ID/两个 hash 齐全且创建出处为空，或上传字段全空、两个创建出处齐全且
`chunk_count=0`；两种出处互斥。创建路径核对原作者、公开回执/资源映射、完整元数据
及永久定位，不产生上传身份。创建准备的 `target_resource_id` 本身是原数值身份，
不是规范媒体外键；来源 pin 只在准备未完成时保护来源。准备和祖先快照保持，规范
目标仅在全部引用检查通过后退役；受保护历史媒体继续检查，引用释放后可回收。
创建原键继续重放原创建结果，不采用上传键的410语义。旧库按
[017创建媒体删除出处](migrations/2026-10-05-infinite-canvas/017-canvas-creation-media-deletions.sql)
精确升级，表数保持99；新库直接使用完整SQL。
2026-10-10 09:53（UTC+8）已在当前 `short_drama` 执行017的一条原子 ALTER，
预检仅缺该升级、无漂移，执行后及独立回读均 ready。该队列表行数0→0，原列内容
摘要相同，表数99→99；没有业务行/媒体清理或服务重启。

canvas_generation_media_deletions 单独记录已处置画布的本人生成媒体退役，不伪造上传记录。
处置回执、任务及调用记录保留 RESTRICT 外键；产物索引 asset_id 与规范媒体 resource_id
作为删除后的原身份保留，两者不是同一个 ID。无外部引用时，本人结果行、产物索引、
规范媒体与 pending 回收记录在同一事务删除/创建；归档清单和公开绑定回执保留。
删除器核对归档清单、checksum、扩展名及完整生成对象路径，跨账号规范定位继续保护
字节，保护释放后仍可重试。状态仅 pending/completed，丢失删除响应不会新建生成。
此表只允许内部维护写入，旧库增量见[015](migrations/2026-10-05-infinite-canvas/015-canvas-generation-media-deletions.sql)。

canvas_library_assets / canvas_library_asset_references 保存私人的源素材文档与其稳定资源关系，不复用标准角色/场景素材表。canvas_library_folders / canvas_library_folder_items 保存个人分类与单一分类归属，分类删除通过关系清理将素材回到未分类。folderId 仅在输出中从关系表投影，避免 JSON 与分类表分别可写。

canvas_project_folders / canvas_project_folder_items 是本人项目文件夹及画布归属，与素材分类分开。`(user_id,source_key)` 和 `(user_id,canvas_id)` 分别唯一；归属以 `(user_id,folder_key)` 复合外键固定到同一作者文件夹，不透明键最多 80 字符。封面使用 media_files/canvas_binary_resources 二选一外键，墓碑禁止继续保留封面引用。文件夹不物理删除，防止旧客户端 PUT 复活；画布真正删除时可级联清除其私人归属，但文件夹删除不级联删除作品。共享文档与共享历史不含 folderId，只有本人投影携带。撤权后的分类清理不授予作品读取/修改权限。

增量升级和预检见[无限画布迁移](migrations/2026-10-05-infinite-canvas/README.md)，已接通的行为见[画布 API](../api/canvases.md)。当前已有上传来源资源的删除保护和回收；其他画布功能进度以实施记录为准。按用户最新边界，标准与画布共用[项目 AI 创作助手](../api/assistant.md)，原源助手的自动改图/生成能力不进入新纯对话助手；私人对话与明确节点媒体引用按项目助手权限冻结，不新增一套画布聊天表。

canvas_creation_attempts / canvas_creation_resources 保存本人首次完整创建的固定请求与资源准备进度。attempt 的 `(user_id,idempotency_key)`、`(user_id,source_key)` 唯一；resource 的目标 ID 与定位值唯一，通过 `(attempt_id,user_id)` 复合外键固定作者。复制期间通过来源媒体/二进制外键防删除，复制完成释放外键并固定来源快照；最终建图、文件与回执同一事务提交。准备过程不创建可见半成品项目。过期清理复用复制锁，先持久标记需重新复制再删除字节，保留请求与目标身份；ready/attached 不进入清理。

canvas_drawings 保存 `(canvas_id,source_key)` 唯一的绘图身份、当前版本及删除墓碑；canvas_drawing_versions 保存 `(drawing_id,row_version)` 唯一的不可变 Excalidraw 文档、预览/成品身份与摘要。版本的 document_json 内 snapshot 采用精确 JSON 字符串封装，并以内部 `$snapshot_encoding` 标记，防止 MySQL JSON 浮点转码改变笔画坐标；读取解码，外部合同仍为原始 snapshot 对象。canvas_drawing_media_references 保护每版实际引用的图片，版本内部引用可随版本 CASCADE，图片外键保持 RESTRICT。当前绘图不维护第二份可编辑全量 JSON，读取由身份表与对应版本组合。已保存作品按项目成员共享，未提交本机草稿不进这些表。

canvas_revision_drawing_references 通过同画布复合外键将整图历史绑定到不可变绘图版本，版本不能在被引用时删除；历史淘汰可级联删除自己的绑定。历史图保存稳定预览定位，恢复绘图使用原版本正文并新增单调递增版本，图、绘图、媒体引用和写回执在同一事务提交。恢复同时检查打开列表时的图版本与绘图版本/墓碑，不能覆盖列表打开后另行保存的笔画。删除绘图仍保留全部版本与资源，版本保留/回收策略及跨项目绘图复制尚须继续迁移。

## 审美与分镜私人运行及阶段批准

`canvas_tool_runs`保存本人、画布、模型与来源的冻结身份，以及45秒租约、持有者与执行epoch。`canvas_tool_submissions`按运行/固定阶段唯一保存不可变请求、批准时间与唯一任务回执；同键恢复不重复生成任务。两表的作者/项目/画布使用复合外键，普通HTTP写入不能伪造授权或替换回执；共享项目成员不能读取他人的创作过程。审美运行以非空源媒体外键保护输入；分镜运行不伪造图片，`source_media_id`为空，来源快照保存原分镜行、画风与资产目录。已批准阶段与任务关联在同一准入事务提交，来源或配置变化阻止尚未执行的新阶段；已受理任务的私人回执仍可核对。

[012分镜来源增量](migrations/2026-10-05-infinite-canvas/012-canvas-storyboard-run.sql)只将该外键列改为可空，保留外键和原记录。2026-10-09已在当前业务库`short_drama`执行，核验`ready/missing=[]/changed=[]`。分镜结果经服务端闭合结构校验，镜头ID由任务ID与顺序确定；绑定复核原行未改变，旧媒体节点保留，失效的逐镜连线删除。项目角色版本接口尚未接通，相关分镜请求明确拒绝，不能把零角色路径通过写成角色验收。
