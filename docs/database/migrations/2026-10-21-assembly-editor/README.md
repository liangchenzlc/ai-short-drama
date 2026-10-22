# 标准成片多轨编辑升级

适用于已完成成片时间轴和声音制作迁移的旧库。新库直接使用[完整 SQL](../../../database/schema/schema.mysql8.sql)，不重复执行本迁移。

执行前备份数据库并暂停相关写入与 render Worker，核对实际目标库和结构；应用不会自动执行 DDL。在已选择正确数据库的连接中执行 [001-editor.sql](001-editor.sql)。SQL 包含两条原子 ALTER，不支持盲目重跑；部分完成时按实际缺项处理。

| 表 | 变更 |
| --- | --- |
| `episode_assembly_clips` | `shot_id` 改为可空；增加 `source_type`、`display_duration_ms`、`transform` 及来源类型／构图对象 CHECK |
| `episode_render_jobs` | 任务类型 CHECK 增加 `transcription`、`audio_analysis` |

现有片段默认 `source_type=shot`，保留所有裁剪、顺序和历史任务；多轨声音、字幕样式与关联保存在既有 `episode_sounds.document` JSON 中，不新增表。声音 v1 与历史渲染快照继续读取，写入 v2 后旧客户端不得覆盖。

2026-10-21 已在当前使用的 **`short_drama`** 实际执行上述两条 ALTER；完整建库 SQL 已从 ORM 更新并通过 `export_schema.py --check`，当前仍为 **107 张表**。独立 MySQL 测试使用随机 `_test` 库，未将业务库作为测试库。其他部署环境仍须独立核验，不能以本机执行记录代替升级。

回读检查 `shot_id` 的可空性、三个新增字段、两个新增 CHECK 和扩展任务类型。部署同版 API、scheduler、render Worker 后启用编辑；需要语音识别时另配 Whisper 或本人云端模型，详见[维护说明](../../../instruction/标准成片编辑器维护说明.md)。已经保存图片／多轨数据后优先向前修复，不能直接回滚旧客户端或删除新字段。
