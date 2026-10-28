# 数据库设计

当前完整结构包含 **119 张表**，以 [SQLAlchemy Domain](../../backend/src/short_drama/domain) 和 [schema.mysql8.sql](schema/schema.mysql8.sql) 为准。后端保留 API → Service → DAO → ORM；ORM 按业务及具体功能组织。

2026-10-28 已在当前配置库执行[退役写作清理](migrations/2026-10-28-source-restructure/README.md)：清空获准测试业务数据，删除 project_novels、project_story_outlines、novel_script_records 三张退役表；保留账号、模型配置、助手偏好、个人 Skill 与有效画布偏好，核对保留数据指纹。不删除 MinIO 对象。旧说明保存在[历史记录](../reviews/2026-10-28-重构前数据库与写作契约记录.md)，其中旧分集、成员与多层素材表不能作为当前结构依据。

同日已在当前库执行[剧本纯文本结构迁移](migrations/2026-10-28-plain-script/README.md)：移除当前剧本及历史快照中旧 Quill 格式列，正文和版本仍是权威数据。

## 当前表目录

下表按 ORM 所在业务包分组；字段、索引、CHECK 与外键细节见完整 SQL，不能仅凭表数判定结构就绪。

| 业务 | 数量 | 表 |
| --- | --- | --- |
| ai_config | 2 | `ai_model_configs`、`user_model_preferences` |
| assistant | 26 | `agent_artifacts`、`agent_attachments`、`agent_batch_undos`、`agent_conversation_summaries`、`agent_conversations`、`agent_discussion_revisions`、`agent_events`、`agent_graph_checkpoints`、`agent_graph_writes`、`agent_media_requests`、`agent_memory_forget_rules`、`agent_memory_items`、`agent_memory_jobs`、`agent_memory_revisions`、`agent_memory_sources`、`agent_messages`、`agent_operations`、`agent_preferences`、`agent_runs`、`agent_skills`、`agent_task_controls`、`agent_task_inputs`、`agent_task_requirements`、`agent_tasks`、`agent_tool_calls`、`agent_turns` |
| canvas | 49 | `canvas_binary_references`、`canvas_binary_resources`、`canvas_channel_models`、`canvas_creation_attempts`、`canvas_creation_resources`、`canvas_director_scenes`、`canvas_drawing_media_references`、`canvas_drawing_versions`、`canvas_drawings`、`canvas_edges`、`canvas_generation_media_deletions`、`canvas_library_asset_references`、`canvas_library_assets`、`canvas_library_folder_items`、`canvas_library_folders`、`canvas_local_jobs`、`canvas_media_references`、`canvas_model_catalogs`、`canvas_node_user_states`、`canvas_nodes`、`canvas_project_folder_items`、`canvas_project_folders`、`canvas_render_jobs`、`canvas_render_media_references`、`canvas_resource_chunks`、`canvas_resource_copy_sources`、`canvas_resource_deletions`、`canvas_resource_uploads`、`canvas_results`、`canvas_revision_drawing_references`、`canvas_revision_media_references`、`canvas_revision_user_states`、`canvas_revisions`、`canvas_style_media_references`、`canvas_style_preferences`、`canvas_style_profiles`、`canvas_task_bindings`、`canvas_task_media_references`、`canvas_task_text_deltas`、`canvas_timelines`、`canvas_tool_runs`、`canvas_tool_submissions`、`canvas_user_binary_references`、`canvas_user_media_references`、`canvas_user_states`、`canvas_workspace_user_states`、`canvas_write_receipts`、`project_canvas_settings`、`project_canvases` |
| creation | 22 | `asset_image_candidates`、`asset_version_media_references`、`asset_versions`、`assets`、`character_voices`、`project_assemblies`、`project_assembly_clips`、`project_render_jobs`、`project_script_revisions`、`project_scripts`、`project_sound_modes`、`project_sounds`、`project_story_settings`、`project_voice_defaults`、`script_shot_records`、`shot_assets`、`shot_dialogues`、`shot_images`、`shot_scripts`、`shot_videos`、`sound_media_references`、`user_creation_drafts` |
| generations | 4 | `ai_generation_records`、`async_tasks`、`generation_batch_items`、`generation_batches` |
| identity | 6 | `audit_events`、`auth_rate_limits`、`email_challenges`、`email_outbox`、`user_sessions`、`users` |
| media | 7 | `image_group_items`、`image_groups`、`image_operations`、`media_assets`、`media_files`、`media_recycle_bin`、`resource_imports` |
| projects | 1 | `projects` |
| skills | 2 | `agent_skill_settings`、`agent_skill_versions` |

## 归属、版本与事务

- projects 由所有者独占，直接拥有剧本、故事设定、素材、分镜与成片，不再存在分集、成员邀请及跨库素材关联。ORM 查询与写入守卫从可信 Actor 推导权限，HTTP 不接受客户端指定拥有者。
- projects.content_version 保护正文与有效编辑稿操作，storyboard_version 保护镜头集合；镜头、素材、草稿与声音分别使用自身行版本。ID 与版本对外均为十进制字符串。
- project_story_settings 四项故事设定使用独立 row_version，读取未保存文档不插库；不与正文共享版本、不自动成为生成提示词。
- 私有 Agent 候选同时校验作者及项目访问权，只有明确采用才改变业务内容。脚本保存、选择及确认不能绕过采用回执。
- project_script_revisions 保存破坏性正文变更的前后内容与版本；恢复产生新版本及恢复前快照，不回退并发令牌，普通按键编辑不逐次记录。
- async_tasks 的幂等请求、生成来源快照、原始输出和业务归档共同支撑恢复；恢复保存复用已收到输出，不重复请求供应商。DAO 不提交事务，组合 Service 共享 Session。
- 媒体保存稳定 MinIO 定位与元数据，签名 URL 不作持久身份。声音与成片上传失败时，独立核对数据库是否已引用，再按准确对象版本补偿清理。
- scope 0–2 的 HTTP 会话入口已退役；现行 V3 引擎、Harness 任务、私有候选和有效运行记录继续维护。已有范围列不因源码命名变更盲目删改。

## 保持完整 SQL 同步

在 backend 目录执行：

```powershell
uv run python scripts/export_schema.py
uv run python scripts/export_schema.py --check
uv run python scripts/check_db_schema.py
```

export_schema 仅生成／比较规范 SQL，不执行 DDL；check_db_schema 当前检查表和列存在，不替代完整类型、默认值、索引及约束核对。实际改表须提供增量迁移并执行，再重新导出。增量清单见[迁移索引](migrations/README.md)。

## 初始化与升级

新库使用 MySQL 8.0.21+、utf8mb4、UTC 及严格 SQL 模式，只在明确选定的空库执行完整 SQL。完整脚本不创建数据库、不选择业务库、不导入数据；循环引用的命名外键在相关表创建后补建。

已有数据库不可用完整脚本覆盖，应用不会自动迁移。按实际结构和迁移记录补齐缺项。获准清空测试数据的本轮清理工具默认只预览，执行模式会验证表清单和保留配置指纹；不能把该工具用于未获授权的数据集。MySQL DDL 隐式提交，数据事务和退役表删除不能描述为可整体回滚。

## 验证

快速测试校验 ORM、导出 SQL 与接口契约；MySQL 集成测试用随机命名且以 _test 结尾的独立库验证锁、并发、外键和约束，结束仅删除该临时库。测试连接需要 CREATE／DROP DATABASE 权限；应用日常连接不因此获得全局权限。RabbitMQ、MinIO 和供应商真实调用分别按测试级别显式启用。
