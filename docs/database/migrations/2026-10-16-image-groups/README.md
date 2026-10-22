# 标准图片组与图片工具迁移

日期：2026-10-16。新增共享作品组、固定图位和本人处理记录，生成图片默认第一张主图。
依赖已有账号协作、素材、分镜、生成任务与媒体结构。新库使用[完整 SQL](../../../database/schema/schema.mysql8.sql)，不叠加本增量。

## 执行与核对

在 `backend/` 执行，Settings 读取当前目录已有 `.env`。先核对目标库并准备可恢复备份，
暂停相关写入进程。MySQL DDL 隐式提交，不能用事务撤销整个迁移。

```powershell
uv run python scripts/image_groups_ddl.py
uv run python scripts/image_groups_ddl.py --apply
uv run python scripts/image_groups_ddl.py --apply
uv run python scripts/export_schema.py --check
```

默认仅输出审阅用 SQL，不连接数据库。`--apply` 显式创建缺失表和两个普通索引，
已存在表核对列集合、类型、可空性、默认值、主键、唯一约束、已有普通索引、外键、
CHECK 表达式和启用状态，以及存储选项；发现不同结构停止，不自动覆盖。
重入时不应再输出“已执行”。[001-image-groups.sql](001-image-groups.sql) 是完整增量，直接运行不幂等。

部署同版 API、scheduler 和相关生成 Worker 后恢复写入，避免旧 Worker 沿用候选归档逻辑。
旧主图首次读取时建立单图兼容组；迁移不批量回写作品、不清除已有候选或任务。

## 本机实际执行

已在当前业务库 **`short_drama`** 实际执行：

1. `CREATE TABLE image_groups`。
2. `CREATE INDEX idx_image_groups_scope`。
3. `CREATE TABLE image_group_items`。
4. `CREATE TABLE image_operations`。
5. `CREATE INDEX idx_image_operations_item`。

完整列、约束与外键见增量 SQL。表数从 **104 增至 107**，后续已运行结构核对并重入，
无待执行语句；完整 SQL 与当前 Domain 为 107 表且检查一致。未改写已有业务作品或调用供应商。
其他环境仍须独立执行，不能沿用本机执行记录。

`image_groups` 以目标和 active_slot 唯一约束维护当前组，历史组 active_slot 为 NULL。
`image_group_items` 固定序号并记录单图版本和原图历史，媒体通过外键保护。
`image_operations` 以作者和原请求键去重，冻结源媒体、版本及摘要；作品共享不授予他人读取私人操作的权限。

产生图片组数据后不应直接降级旧 API／Worker，也不要自动 DROP 新表。
需要回退时先停写、导出新增数据及作品引用，按备份和实际写入状态人工制定恢复步骤；
优先向前修复，不删除媒体来绕过外键。

数据库与工具验证使用随机隔离 `_test` 库和本地供应商／存储替身，包含完整建库、生成归档、工具处理、
主图冲突与权限。另已显式启用并通过 3 项基础设施检查，使用真实 RabbitMQ／Celery／MySQL／MinIO，
覆盖公共生成归档、分镜图自动主图及旧接口并发重放。供应商仍为本地替身，
未覆盖所有图片工具的真实供应商组合，不能作为收费模型或画布一比一验收。
接口与限制见[图片组 API](../../../api/image-groups.md)。
