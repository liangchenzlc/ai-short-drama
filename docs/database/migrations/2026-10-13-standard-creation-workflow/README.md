# 标准模式创作流程：名称、格式与私人生成草稿

新库执行 [完整 SQL](../../../database/schema/schema.mysql8.sql)。旧库已具备此前写作、协作、媒体和角色历史结构后，执行 [001-standard-creation.sql](001-standard-creation.sql)。脚本新增 `shot_scripts.title/shot_size`、`episode_scripts.editor_document` 和 `user_creation_drafts`，不删除作品、候选或历史关联。

若已经执行部分新增字段，使用 `backend/` 下的 `uv run python scripts/standard_creation_ddl.py --apply` 按缺失结构升级。可用 `--db-port` 覆盖实际本机端口，保留既有配置。脚本使用 Settings 的库名与凭据，输出库名和执行语句；应用启动不自动迁移。MySQL DDL 隐式提交，代码回退时保留新增数据。

`title` 长度上限 255，历史记录默认空字符串；`shot_size` 可空且仅允许 `full_shot/medium_shot/close_up/extreme_close_up/wide_shot/two_shot`。名称与景别修改使用原镜头版本；标题不参与媒体内容上下文摘要。

正文与受限 Quill Delta 同事务、同 `content_version` 保存。Delta 只接受文本、标题 1–3、粗体、斜体和引用，文本拼接后只去掉一个 Quill 结尾换行，必须与 `content` 一致。旧客户端省略或提交空格式时清除旧 Delta。候选与提取、生成、导出继续使用纯文本。

私人草稿唯一范围为本人、项目、分集、稳定对象和用途；本人从可信 Actor 推导，客户端不提交用户 ID。用途为 `novel_adaptation/asset_image/storyboard_image/storyboard_video`，小说对象为当前分集 ID。图片和小说改编输入最多 4000 字符，视频输入最多 16000 字符；新增和合并后的版本化修改均按用途校验。模型选择可空以保存未完成输入；生成必须明确选择可用且本人可访问的模型。草稿不保存凭据和展示 URL，参考图只保存永久媒体 ID，读取时重新提供临时展示 URL。`row_version/draft_version/source_version` 均以十进制字符串对外返回。

生成通过 `source.draft_id/draft_version` 显式引用并锁内冻结私人输入和参数。`source_version` 记录草稿保存时溯源；任务另记当前来源版本，原请求的正文／对象版本、上下文摘要及采用图仍独立校验。图片补充提示词最多 4000 字符，共享与私人参考图按永久 ID 去重后最多 16 张；视频仍引用当前采用分镜图。私人参数不写回共享作品字段，候选仍须明确采用。

素材类型变更使用已有 `assets.kind`，无新增列。场景时间须兼容目标类型；角色不可变作品历史、声音绑定或对白引用会阻止转换。通用分镜关联保留，合法转换取消旧确认并使受影响媒体按内容上下文标记过期。

2026-10-13 已在当前本机 Docker MySQL `127.0.0.1:13306` 的 `short_drama` 库执行：两个镜头列及景别 CHECK、剧本 Delta 列及 JSON 对象 CHECK、私人草稿完整表及范围唯一键、外键、JSON／版本 CHECK 与两个索引；随后用 `ALTER TABLE user_creation_drafts MODIFY COLUMN input_text MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT ...` 同步分用途输入上限说明，字段类型与已有数据保持原值。已重入核对缺失结构，重复执行不会新增相同列、约束或索引。未读取或输出配置文件内容，没有对应用库运行测试。隔离集成测试结果由实施验收记录单独说明。

此前工作区的 [docs/database 同名 P0 SQL](../../../database/migrations/2026-10-13-p0-contract/001-p0-contract.sql) 已同步为同一增量合同，保留路径便于已有使用者查找；执行其中一份即可。

2026-10-14 重新核对当前 Settings 指向的 `short_drama@127.0.0.1:3306`（MySQL 8.4.11）：Docker 引擎恢复后，该库尚无上述三个字段与私人草稿表；2026-10-13 的 `13306` 历史执行记录不能代替本轮当前库证据。已在 `backend/` 使用现有 `.tools/uv/uv.exe run python scripts/standard_creation_ddl.py --apply` 实际执行以下 8 条增量语句，保留已有作品和关联：

1. 为 `shot_scripts` 增加非空 `title VARCHAR(255)`，旧记录默认空字符串。
2. 为 `shot_scripts` 增加可空 `shot_size VARCHAR(20)`。
3. 增加 `ck_shot_scripts_shot_size` 景别枚举 CHECK。
4. 为 `episode_scripts` 增加可空 `editor_document JSON`。
5. 增加 `ck_scripts_editor_document` JSON 对象 CHECK。
6. 创建完整 `user_creation_drafts` 表，含范围唯一键、三个外键、JSON/用途/版本 CHECK 和个人参考媒体列。
7. 创建 `idx_user_creation_drafts_user_episode`。
8. 创建 `idx_user_creation_drafts_user_project`。

随后对当前库执行同一 `apply_ddl` 重入，新增语句为 **0 条**；元数据断言确认字段类型/空值/默认值、私人草稿全部列、范围唯一键、三个外键、六个 CHECK 和两个业务索引与现有 ORM/完整 SQL 合同一致。执行日志位于被忽略的 `backend/.runtime/standard-backend-ddl-applied-20261014.log`，幂等与元数据核对位于 `backend/.runtime/standard-backend-ddl-reentry-metadata-20261014.log`。应用库未作为测试库，未读取或输出 `.env` 内容；本轮随机 MySQL 集成结果及环境边界由实施验收记录单独说明。
