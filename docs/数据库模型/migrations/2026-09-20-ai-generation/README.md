# 模型生成与资产库增量 SQL

本目录保留为历史增量升级记录，用于尚未升级的旧 `ai_short_drama` 数据库。使用 MySQL 8.0.21+；已有 `ai_model_configs`、`media_files` 必须与当前项目表结构兼容。新数据库只执行[完整建表脚本](../../schema.mysql8.sql)，其中已包含全部 21 张表、配置缓存及五种任务状态，不再叠加执行本目录脚本。已完成迁移的业务库也无需重跑。

## 执行顺序

在数据库客户端打开文件，按下列顺序逐个执行整个文件；每个文件均包含 `USE ai_short_drama`，执行前必须核对该库名；目标库名不同时，在审阅副本中调整，不能仅依赖客户端默认选中的数据库。

| 顺序 | 文件 | 作用 |
|---|---|---|
| 0 | [000_precheck.sql](000_precheck.sql) | 只读检查版本、依赖表及是否已执行过 |
| 1 | [001_create_async_tasks.sql](001_create_async_tasks.sql) | 创建模型生成任务表，19 个字段 |
| 2 | [002_create_ai_generation_records.sql](002_create_ai_generation_records.sql) | 创建模型调用记录表，17 个字段 |
| 3 | [003_create_media_assets.sql](003_create_media_assets.sql) | 创建生成图片/视频资产表，9 个字段 |
| 4 | [004_add_model_capability_cache.sql](004_add_model_capability_cache.sql) | 给 AI 配置增加可空 JSON 缓存字段 |
| 5 | [005_verify.sql](005_verify.sql) | 只读核对建表结果 |
| 6 | [006_remove_task_unknown.sql](006_remove_task_unknown.sql) | 已建库升级：旧任务 unknown 改为 failed，并收紧为五种任务状态 |

`006` 是旧六状态任务的升级脚本。执行时暂停 API、调度器和 Worker；保留历史错误、调用记录和资产，失效旧消息版本。调用记录内部的受理不明证据保留，失败任务也不会因此自动重复调用模型。新建库按当前 `001` 已采用五种任务状态，无需执行 `006`。

## 执行前核对

运行 `000_precheck.sql` 后，检查：

- 数据库为 `ai_short_drama`，版本是 MySQL 8.0.21 或以上，SQL 模式包含 `STRICT_TRANS_TABLES` 或 `STRICT_ALL_TABLES`。
- 两张依赖表均为 InnoDB，`id` 均为非空的 `BIGINT UNSIGNED` 主键。
- 首次执行时，三张新表和 `capability_cache` 字段的查询结果均为空。

检查文件只是 SELECT，不会自动阻止你运行后续文件。若上述条件不符合，应先核对当前库结构，不要继续执行建表文件。

## 执行与恢复

`001` 至 `004` 各包含一条结构变更，按编号执行一次即可。脚本没有 `DROP`、`TRUNCATE`、业务数据修改或账号权限修改；也不会调整已有分镜图片/视频表。

刻意不使用 `CREATE TABLE IF NOT EXISTS` 跳过检查，避免把同名但结构不同的表误认为迁移完成。MySQL DDL 会隐式提交，四个文件不是一个整体事务；某个文件失败时停止，保留已成功执行的文件，从失败处核对原因。若提示表或列已存在，先用核对脚本确认定义，不要删表或盲目重复执行。

执行 `005_verify.sql` 后应看到：三张表的列数依次为 19、17、9；`capability_cache` 为可空 JSON；五条外键及完整表定义。完整定义应与 `001` 至 `003` 中的字段、索引和约束一致，列数相同本身不代表结构完全一致。

## 与业务的关系

- 所有主键由应用雪花算法生成，SQL 不使用 `AUTO_INCREMENT`，无需手动填充测试记录。
- 三个生成接口共用这三张新表，业务来源保存在调用记录的请求快照。
- 生成图片成功后进入资产库；只有用户选中并确认采用时，应用才更新 `shot_images`。本目录不改动它的现有约束。
- 应用负责维护 UTC 时间及 `updated_at`；采用失败不会触发新的模型生成。

本批脚本对应的最终结构已合并至[完整建表脚本](../../schema.mysql8.sql)。归档独立预算、投递暂停与安全恢复已在应用层实现，不需要增加表字段，运行步骤见[开发说明](../../../development.md)。
