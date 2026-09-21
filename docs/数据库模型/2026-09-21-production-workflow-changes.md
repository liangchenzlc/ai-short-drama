# 五阶段业务接入：数据库变更设计（已批准）

配套：[总体设计](../superpowers/specs/2026-09-21-production-workflow-design.md)、[接口文档](../api/2026-09-21-production-workflow-api.md)。

本文件仅是变更设计。此轮不修改 `schema.mysql8.sql`，不执行 ALTER/CREATE；审批实施时同步规范建表 SQL、SQLAlchemy Domain 和迁移文件。

## 1. 保持的数据关系

```text
episodes → episode_novels / episode_scripts / shot_scripts
episode_novels → novel_script_records → episode_scripts
episode_scripts → script_shot_records → shot_scripts
assets ← global_assets / project_assets / episode_assets / shot_assets
assets → asset_image_candidates → media_files
shot_scripts → shot_images → media_files
async_tasks → ai_generation_records → media_assets → media_files
```

不再建任务表、消息表或生成结果总表。不增加供应商表、提示词平台或多套业务任务状态。
所有业务主键继续使用应用雪花算法；Idempotency-Key 是请求去重标识，不能替代业务主键。

## 2. `episodes`：分镜集合版本

| 新增字段 | 类型/默认 | 用途 |
| --- | --- | --- |
| storyboard_version | BIGINT UNSIGNED NOT NULL DEFAULT 1 | 分镜增改、素材关联、排序、归档、应用候选、图片采用的集合并发版本 |

新增 `CHECK (storyboard_version > 0)`。小说/剧本继续使用已存在的 content_version，不让分镜保存与小说输入互相制造冲突。
素材本体修改不批量推进所有引用分集的版本；分镜图片时效通过上下文摘要判断。

## 3. `shot_scripts`：最小镜头扩展

| 新增字段 | 类型/默认 | 用途 |
| --- | --- | --- |
| row_version | BIGINT UNSIGNED NOT NULL DEFAULT 1 | 单镜头的保存、归档与采用保护 |
| image_settings | JSON NULL DEFAULT NULL | 闭合对象 resolution/aspect/layout；NULL按2K/inherit/single读取 |
| deleted_at | DATETIME(6) NULL DEFAULT NULL | 归档时间；非空行不参与当前分镜列表 |
| active_position | INT UNSIGNED GENERATED ALWAYS AS (...) STORED | 未归档时等于position，否则NULL，用于活动顺序唯一约束 |
| creation_key | VARCHAR(128) COLLATE utf8mb4_0900_bin NULL | 手动新建的幂等键；生成输出与历史行为空 |
| creation_hash | CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL | 不可变的初始创建请求摘要；包含项目/分集范围 |

`image_settings` 不存 UI 状态、任务状态或当前已采用图片。API禁止任意 JSON 键；数据库检查为 JSON object 或 NULL。
`creation_key/hash` 必须同时为空或同时非空，非空键去首尾空格后必须有内容，摘要长度64。`UNIQUE(creation_key)`允许历史NULL。

生成列和索引设计：

```sql
-- 仅展示目标定义；实施迁移需先增加新约束，校验后再移除旧约束。
`active_position` INT UNSIGNED GENERATED ALWAYS AS
  (CASE WHEN `deleted_at` IS NULL THEN `position` ELSE NULL END) STORED,
UNIQUE KEY `uk_shots_episode_active_position` (`episode_id`, `active_position`),
KEY `idx_shots_episode_deleted_position` (`episode_id`, `deleted_at`, `position`, `id`)
```

原 `uk_shots_episode_position(episode_id,position)` 改为上述活动索引。原 `uk_shots_id_episode(id,episode_id)` 保留，供 shot_assets/images/videos 的复合外键使用。
原 position>0 约束保留。row_version>0、归档时间不早于已知创建时间、新建键摘要成对约束补齐。

排序算法：锁分集及活动镜头，验证集合完整；先把待变更行移到不冲突的临时正整数区间，再设最终1..N，同事务完成。使用最大 position 检查 UINT 上界，不用负数，也不能依赖MySQL逐行UPDATE顺序规避唯一冲突。
替换时先归档旧镜头，使 active_position 变为NULL，再插入新1..N。归档保留原position便于历史查看。

为什么不用直接删除：script_shot_records、shot_assets、shot_images、shot_videos、media_recycle_bin 均可能引用旧镜头；归档保留完整回溯关系，不做级联物理删除。

## 4. `assets`：真实表单和共享编辑

| 新增字段 | 类型/默认 | 用途 |
| --- | --- | --- |
| row_version | BIGINT UNSIGNED NOT NULL DEFAULT 1 | 元信息和采用并发保护 |
| state | VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'unconfirmed' | unconfirmed/confirmed；只表示素材确认 |
| tags | JSON NOT NULL DEFAULT (JSON_ARRAY()) | 当前前端已有的多标签，最多20个 |
| scene_time | VARCHAR(60) NOT NULL DEFAULT '' | 场景时间，避免拼接description |
| creation_key | VARCHAR(128) COLLATE utf8mb4_0900_bin NULL | 手动创建幂等键，跨库创建键保持唯一 |
| creation_hash | CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL | 初始创建内容和目标库范围的不可变摘要 |

分类沿用 label，不新增 category 同义字段。description/prompt 沿用原字段，互不覆盖。
约束：版本>0；state枚举；confirmed 必须 media_id 非空；tags 为数组且长度≤20；非scene的scene_time必须为空；creation_key/hash成对有效；creation_key唯一。
tags每项字符串/40字符/去重在Pydantic层检查，SQL不得把多标签逗号拼入label。

旧素材统一 backfill 为 unconfirmed，即使已有关联图片也不推测用户曾确认；media_id保留。新增候选关联时包含其原media_id，不删除或换图。
共享编辑只推进本体版本；查询实时返回引用数。多个库共享同一行，确认前展示影响范围。
库移除只删除global/project/episode关联；assets和已上传图片本期不提供硬删除/自动垃圾回收，避免最后一个入口移除后误删历史。

## 5. 新表 `asset_image_candidates`

保存素材的可选图片关系；无需把上传图片伪装成AI输出。

```sql
CREATE TABLE `asset_image_candidates` (
  `id` BIGINT UNSIGNED NOT NULL COMMENT '应用雪花ID',
  `asset_id` BIGINT UNSIGNED NOT NULL COMMENT '角色/场景/道具本体',
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
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_0900_ai_ci COMMENT='素材参考图片候选';
```

生成来源通过media_files→media_assets→ai_generation_records查出，上传图片没有对应生成记录。候选元信息不复制原文件尺寸、URL和模型信息。
同文件上传去重：计算SHA256与字节数，提交时锁定asset，再在其候选关联中查相同摘要及长度。并发重复只保留一个候选，冗余新对象补偿删除。checksum不是跨全库唯一身份。

历史 `assets.media_id` 候选回填需要Snowflake：实施提供Python回填命令，不用AUTO_INCREMENT或SQL UUID当BIGINT。读取不能隐式补建候选。

## 6. `shot_images`：采用时上下文

新增 `context_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL`，非空时长度64。
摘要规范版本为 `shot-context-v1`，对规范化JSON的UTF-8字节做SHA256，包含：

```text
version, shot_id, shot.script,
episode.aspect, episode.style,
assets(按id排序): id, kind, name, label, description, prompt,
                 tags(排序后), scene_time, state, media_id
```

null使用JSON null；文本保留内容，key顺序固定，不包含临时URL、时间戳、row_version、UI展开状态、下一次生成设置。
修改下一次生成的分辨率不会把已采用图片误标过期；修改实际创作内容/参考图会。
素材标签、分类、场景时间进入提示词和摘要，避免内容遗漏。
新采用保存当时用户确认的上下文；历史NULL展示“需重新核对”。用户明确采用旧来源图片后记录当前已核对上下文，原生成快照仍在生成记录中。

## 7. 复用任务、结果与回溯表

`async_tasks` 不增字段、不新增状态、不新增队列。
`ai_generation_records` 使用既有request_data/response_data，不加影子记录表：

```text
request_data.source:
  scene, project_id, episode_id, source_id,
  novel_id/script_id/shot_id（与scene一致）
request_data.source_snapshot:
  来源正文、来源内容hash、提交时版本、风格/画幅、素材快照
request_data.template_version: novel-script-v1 / script-shots-v1 / shot-image-v1
request_data.input: 后端最终生成的messages或prompt/reference_media_ids
request_data.business_intent: 原始业务补充要求（用于追溯，不含密钥）
response_data.business_result:
  kind, schema_version, script_id 或 shots,
  applied（分镜采用结果或null）
```

source中的请求content_version和当前版本分开；快照hash用于判断语义变化，不因只切换过编辑指针又切回来就谎称正文变化。
来源JSON路径由服务端生成，DAO筛选使用固定白名单路径；本期先沿用现有SQL JSON筛选，不增一组冗余project/episode/source列。数据量增长时根据EXPLAIN加索引，不能在前端过滤分页。

业务结果保存复用next_action=save；原文先持久化，随后结果校验/本地保存支持崩溃恢复。response_data更新按原对象合并，不覆盖usage、finish_reason、归档信息。
`novel_script_records.batch_id`、`script_shot_records.batch_id` 对新AI业务取task.id；原历史批次保留，不新增外键约束历史值。
同任务处理标记和业务输出同事务提交，受task行锁保护；不依赖“查不到记录就插入”的无锁判重。
小说候选入库专用DAO不修改editing_script_id、不推进content_version；既有手动首次保存行为保持不变。

## 8. 迁移交付与验收

审批实施时交付目录 `migrations/2026-09-21-production-workflow/`：

1. `000_precheck.sql`：MySQL≥8.0.16、字段/约束现状、position冲突、素材媒体有效性、引用与行数统计。
2. `001_episode_storyboard.sql`：分集版本、镜头字段与活动顺序索引；先验证新索引再移除旧索引。
3. `002_asset_persistence.sql`：素材版本/标签/确认/创建幂等字段、素材图片候选表。
4. `003_shot_image_context.sql`：采用上下文摘要。
5. `004_backfill_asset_candidates.py`：分批、可重入、Snowflake生成，已有组合跳过；坏媒体引用停止并报告，不能静默丢弃。
6. `005_verify.sql`：字段类型、默认、索引、CHECK启用、原行数和关联完整性、候选回填、版本非零。
7. `README.md`：执行顺序、写入暂停窗口、备份、重入条件、回退限制。

应用顺序：备份元数据及受影响表→暂停API写入/worker保存→迁移/回填→核验→更新API/worker→恢复写入→只读健康检查及专用数据验收。
MySQL DDL不保证整个迁移原子回滚；脚本检查已存在字段/约束，允许按文档重入，但不覆盖业务新数据。
不得直接用新建库schema覆盖现有数据库。实施同时更新 `schema.mysql8.sql`、Domain与 `MySQL8数据表设计.md`；规范schema与迁移后实际结构必须对照验证。
已有归档/共享/生成数据后，不执行删除新列、重建旧唯一索引的自动回退；先保留备份，必要时修正迁移和应用向前恢复。

测试库必须独立且以 `_test` 结尾，不在用户业务库运行清表式测试，不在本地安装MySQL。
