# 持久批量生成

前提：已部署异步生成、素材/分镜及时间轴迁移。新增 `generation_batches`、`generation_batch_items`，保留子任务、重试批次与来源快照，不改既有候选采用规则。

先备份，在 `backend` 运行 `python scripts/generation_batch_ddl.py --apply`。脚本只创建缺失表，可重复执行。未带 `--apply` 只输出 DDL；`001_generation_batches.sql` 用于审阅或一次性手工升级。

API、调度器、worker 更新一致后设置 `GENERATION_BATCHES_ENABLED=true`。回退时关闭开关并保留表与历史任务；不要删除记录恢复旧版本。具体限流与恢复行为见[制作功能说明](../../../production-features.md)。
